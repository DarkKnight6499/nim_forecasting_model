"""
Acceptance tests for Phase 10: NSFR (core/nsfr.py), scenario-stressed LCR
(core/lcr.py compute_lcr(stressed=True)), and capital-lite CET1/RWA
(core/capital.py), plus the DIVIDEND_PAYOUT_RATIO that supersedes the old
RETENTION_RATIO (core/engine.py).

Run with: py -m pytest tests/ -q
"""

import copy

import pytest

import config
from core import capital, engine, lcr, nsfr
from core.balance_sheet import load_positions
from core.position import Position
from curve import shocks
from curve.scenarios import build_curve_scenarios
from curve.yield_curve import YieldCurve


# ---------------------------------------------------------------------------
# 1. Toy-book NSFR hand-calculation; default book NSFR sane across scenarios.
# ---------------------------------------------------------------------------

def _toy_liability(name, category_type, balance, **kwargs):
    return Position(name=name, side="liability", category_type=category_type, balance=balance, rate=0.03, **kwargs)


def _toy_asset(name, category_type, balance, **kwargs):
    return Position(name=name, side="asset", category_type=category_type, balance=balance, rate=0.05, **kwargs)


def test_toy_book_nsfr_matches_hand_calculation():
    positions = [
        _toy_liability("A stable retail", "administered", 1000, lcr_outflow_category="stable_retail"),
        _toy_liability("B less stable retail", "administered", 500, lcr_outflow_category="less_stable_retail"),
        _toy_liability("C term deposit ladder", "laddered", 600, ladder_months=24, lcr_outflow_category="term_deposit"),
        _toy_liability("D wholesale overnight", "variable", 300, lcr_outflow_category="wholesale_non_operational"),
        _toy_liability("E wholesale ladder", "laddered", 900, ladder_months=36, lcr_outflow_category="wholesale_non_operational"),
        _toy_asset("F cash", "variable", 100, calibration_category="cash"),
        _toy_asset("G L1 securities", "laddered", 400, ladder_months=36, hqla_level="L1", calibration_category="securities"),
        _toy_asset("H L2A securities", "laddered", 300, ladder_months=36, hqla_level="L2A", calibration_category="securities"),
        _toy_asset("I L2B securities", "laddered", 200, ladder_months=36, hqla_level="L2B", calibration_category="securities"),
        _toy_asset("J long loan", "fixed_amortizing", 800, cpr_annual=0.05, calibration_category="loans"),
        _toy_asset("K mortgage override", "fixed_amortizing", 500, cpr_annual=0.05, calibration_category="loans", rsf_factor_override=0.65),
        _toy_asset("L revolving loan", "variable", 250, calibration_category="loans"),
    ]
    balances = {p.name: p.balance for p in positions}
    equity = 200.0

    result = nsfr.compute_nsfr(positions, balances, equity)

    expected_asf = (
        equity
        + 1000 * 0.95
        + 500 * 0.90
        + 600 * (0.5 * 1.0 + 0.5 * 0.90)   # ladder_months=24: half the ladder >= 12 months
        + 300 * 0.50
        + 900 * ((24 / 36) * 1.0 + (12 / 36) * 0.50)  # ladder_months=36: 24/36 of the ladder >= 12 months
    )
    expected_rsf = (
        100 * 0.00
        + 400 * 0.05
        + 300 * 0.15
        + 200 * 0.50
        + 800 * 0.85    # avg life 1/0.05=20y > 1y
        + 500 * 0.65    # override wins over the general performing-loan rate
        + 250 * 0.50    # variable-rate loan, no amortization schedule: treated <1y
    )

    assert result["asf"] == pytest.approx(expected_asf, rel=1e-9)
    assert result["rsf"] == pytest.approx(expected_rsf, rel=1e-9)
    assert result["nsfr"] == pytest.approx(expected_asf / expected_rsf, rel=1e-9)


def test_default_book_nsfr_above_one_at_month_zero_and_never_nan():
    positions = load_positions()
    base_curve = YieldCurve(config.FALLBACK_CURVE_TENORS, config.FALLBACK_CURVE_RATES)
    curve_paths = build_curve_scenarios(base_curve, config.RATE_SCENARIOS, horizon_months=24, ramp_months=12)
    combined_summary, details_by_scenario, _ = engine.run_all_scenarios(positions, curve_paths)

    month0_balances = {p.name: p.balance for p in positions}
    month0_equity = combined_summary.loc[
        (combined_summary["scenario"] == "Base (flat)") & (combined_summary["month"] == 0), "equity"
    ].iloc[0]
    month0_result = nsfr.compute_nsfr(positions, month0_balances, month0_equity)
    assert month0_result["nsfr"] > 1.0

    for label, detail_df in details_by_scenario.items():
        summary = combined_summary[combined_summary["scenario"] == label]
        for month in sorted(detail_df["month"].unique()):
            balances = detail_df.loc[detail_df["month"] == month].set_index("bucket")["balance"].to_dict()
            equity = summary.loc[summary["month"] == month, "equity"].iloc[0]
            result = nsfr.compute_nsfr(positions, balances, equity)
            assert result["nsfr"] == result["nsfr"]  # not NaN
            assert result["nsfr"] > 0


# ---------------------------------------------------------------------------
# 2. Stressed LCR <= base LCR, every month, every scenario.
# ---------------------------------------------------------------------------

def test_stressed_lcr_never_exceeds_base_lcr():
    positions = load_positions()
    base_curve = YieldCurve(config.FALLBACK_CURVE_TENORS, config.FALLBACK_CURVE_RATES)
    curve_paths = build_curve_scenarios(base_curve, config.RATE_SCENARIOS, horizon_months=24, ramp_months=12)
    _, details_by_scenario, _ = engine.run_all_scenarios(positions, curve_paths)

    for label, detail_df in details_by_scenario.items():
        for month in sorted(detail_df["month"].unique()):
            balances = detail_df.loc[detail_df["month"] == month].set_index("bucket")["balance"].to_dict()
            base_result = lcr.compute_lcr(positions, balances)
            stressed_result = lcr.compute_lcr(positions, balances, stressed=True)
            assert stressed_result["lcr"] <= base_result["lcr"] + 1e-9


def test_stress_multipliers_capped_at_one():
    positions = [_toy_liability("Wholesale", "variable", 1000, lcr_outflow_category="wholesale_non_operational")]
    balances = {"Wholesale": 1000}
    # A synthetic multiplier that would push the factor past 1.0 without the cap.
    original = dict(config.LCR_STRESS_MULTIPLIERS)
    config.LCR_STRESS_MULTIPLIERS["wholesale_non_operational"] = 10.0
    try:
        outflows, _ = lcr.compute_outflows(positions, balances, stressed=True)
    finally:
        config.LCR_STRESS_MULTIPLIERS.clear()
        config.LCR_STRESS_MULTIPLIERS.update(original)
    assert outflows == pytest.approx(1000.0)  # capped at 100% of balance, not 400%


# ---------------------------------------------------------------------------
# 3. Dividend payout: balance sheet identity holds; payout 0.0 dominates 0.3;
#    CET1 ratio orders the same way as equity.
# ---------------------------------------------------------------------------

def test_balance_sheet_identity_holds_with_dividends_on():
    positions = load_positions()
    base_curve = YieldCurve(config.FALLBACK_CURVE_TENORS, config.FALLBACK_CURVE_RATES)
    curve_path = build_curve_scenarios(base_curve, {"Base": shocks.parallel(0)}, horizon_months=24, ramp_months=12)["Base"]
    summary_df, detail_df, _ = engine.run_scenario(copy.deepcopy(positions), curve_path)

    for month in sorted(detail_df["month"].unique()):
        snap = detail_df[detail_df["month"] == month]
        assets = snap.loc[snap["side"] == "asset", "balance"].sum()
        liabs = snap.loc[snap["side"] == "liability", "balance"].sum()
        equity = summary_df.loc[summary_df["month"] == month, "equity"].iloc[0]
        assert abs(assets - liabs - equity) < 1.0


def test_zero_payout_equity_path_strictly_dominates_default_payout():
    positions = load_positions()
    base_curve = YieldCurve(config.FALLBACK_CURVE_TENORS, config.FALLBACK_CURVE_RATES)
    curve_path = build_curve_scenarios(base_curve, {"Base": shocks.parallel(0)}, horizon_months=24, ramp_months=12)["Base"]

    summary_full_retention, detail_full_retention, _ = engine.run_scenario(
        copy.deepcopy(positions), curve_path, dividend_payout_ratio=0.0
    )
    summary_default_payout, detail_default_payout, _ = engine.run_scenario(
        copy.deepcopy(positions), curve_path, dividend_payout_ratio=config.DIVIDEND_PAYOUT_RATIO
    )
    assert config.DIVIDEND_PAYOUT_RATIO > 0.0

    for month in range(1, 24):
        equity_full = summary_full_retention.loc[summary_full_retention["month"] == month, "equity"].iloc[0]
        equity_default = summary_default_payout.loc[summary_default_payout["month"] == month, "equity"].iloc[0]
        assert equity_full > equity_default

        balances_full = detail_full_retention.loc[detail_full_retention["month"] == month].set_index("bucket")["balance"].to_dict()
        balances_default = detail_default_payout.loc[detail_default_payout["month"] == month].set_index("bucket")["balance"].to_dict()
        cet1_full = capital.compute_cet1_ratio(positions, balances_full, equity_full)["cet1_ratio"]
        cet1_default = capital.compute_cet1_ratio(positions, balances_default, equity_default)["cet1_ratio"]
        assert cet1_full > cet1_default


# ---------------------------------------------------------------------------
# 4. RWA responds to balance-sheet mix.
# ---------------------------------------------------------------------------

def test_rwa_rises_when_mix_shifts_toward_higher_density_assets():
    positions = [
        _toy_asset("High density", "variable", 1000, rwa_density=1.00),
        _toy_asset("Zero density", "variable", 1000, rwa_density=0.00),
    ]
    balances_before = {p.name: p.balance for p in positions}
    rwa_before, _ = capital.compute_rwa(positions, balances_before)

    shift = 200.0
    balances_after = dict(balances_before)
    balances_after["High density"] += shift
    balances_after["Zero density"] -= shift
    rwa_after, _ = capital.compute_rwa(positions, balances_after)

    assert rwa_after > rwa_before
    assert rwa_after - rwa_before == pytest.approx(shift * 1.00)
