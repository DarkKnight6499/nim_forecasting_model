"""
Acceptance tests for LCR (core/lcr.py), the joint LCR-NIM view
(core/joint_view.py), and the AFS mark-to-market buffer (core/mtm.py).
Run with: py -m pytest tests/ -q
"""

import numpy as np
import pytest

import config
from curve import shocks
from curve.yield_curve import YieldCurve
from curve.scenarios import build_curve_scenarios
from core.balance_sheet import load_positions
from core.position import Position
from core import engine, lcr, mtm, joint_view
from core.ftp import aggregate as ftp_aggregate


def _default_curve_paths(months=24, ramp=12, scenario_defs=None):
    base_curve = YieldCurve(config.FALLBACK_CURVE_TENORS, config.FALLBACK_CURVE_RATES)
    scenario_defs = scenario_defs or config.RATE_SCENARIOS
    return build_curve_scenarios(base_curve, scenario_defs, months, ramp)


# ---------------------------------------------------------------------------
# 1. LCR unit tests against hand-computable toy books, including the L2B cap.
# ---------------------------------------------------------------------------

def test_compute_lcr_matches_hand_calculation_no_caps():
    positions = [
        Position(name="Cash", side="asset", category_type="variable", balance=1000, rate=0.01, hqla_level="L1"),
        Position(name="Bond", side="asset", category_type="laddered", balance=100, rate=0.02, ladder_months=12, hqla_level="L2A"),
        Position(name="Deposit", side="liability", category_type="administered", balance=2000, rate=0.01, lcr_outflow_category="stable_retail"),
    ]
    balances = {p.name: p.balance for p in positions}
    result = lcr.compute_lcr(positions, balances)

    assert result["hqla"] == pytest.approx(1000 + 100 * 0.85)
    assert result["gross_outflows"] == pytest.approx(2000 * 0.05)
    assert result["net_outflows"] == pytest.approx(100)
    assert result["lcr"] == pytest.approx((1000 + 85) / 100)


def test_lcr_l2b_cap_binds_with_excess_l2b():
    positions = [
        Position(name="Cash", side="asset", category_type="variable", balance=100, rate=0.01, hqla_level="L1"),
        Position(name="Munis", side="asset", category_type="laddered", balance=1000, rate=0.03, ladder_months=12, hqla_level="L2B"),
    ]
    balances = {p.name: p.balance for p in positions}
    hqla_detail = lcr.compute_hqla(positions, balances)

    l2b_after_haircut = 1000 * (1 - config.LCR_HQLA_HAIRCUTS["L2B"])
    expected_l2b_capped = (config.LCR_L2B_CAP_OF_HQLA / (1 - config.LCR_L2B_CAP_OF_HQLA)) * 100

    assert hqla_detail["l2b_capped"] == pytest.approx(expected_l2b_capped)
    assert hqla_detail["l2b_capped"] < l2b_after_haircut
    assert hqla_detail["hqla"] == pytest.approx(100 + expected_l2b_capped)


# ---------------------------------------------------------------------------
# 2. Default balance sheet's LCR lands in a plausible range, never NaN.
# ---------------------------------------------------------------------------

def test_default_balance_sheet_lcr_is_plausible_and_never_nan():
    # This synthetic bank is deposit-funded with little short-term wholesale
    # reliance (most of its liabilities are administered NMDs or long-ladder
    # term funding, both of which draw a small 30-day outflow relative to its
    # sizeable HQLA book), so its LCR genuinely sits well above a typical
    # 110-160% real-world target - the bound here is a sanity check (positive,
    # finite, not an absurd outlier), not a claim that ~100-160% is expected.
    positions = load_positions()
    paths = _default_curve_paths()
    for label, path in paths.items():
        _, detail_df, _ = engine.run_scenario(positions, path, scenario_label=label)
        for month in sorted(detail_df["month"].unique()):
            balances = detail_df[detail_df["month"] == month].set_index("bucket")["balance"].to_dict()
            result = lcr.compute_lcr(positions, balances)
            assert not np.isnan(result["lcr"]), f"{label} month {month}: LCR is NaN"
            if month == 0:
                assert 1.0 <= result["lcr"] <= 6.0, f"{label} month 0: LCR {result['lcr']:.2f} outside plausible range"


# ---------------------------------------------------------------------------
# 3. Joint view: NIM contributions sum to total NIM; a removed liability
#    changes the LCR denominator by exactly its own outflow contribution.
# ---------------------------------------------------------------------------

def test_joint_view_nim_contributions_sum_to_total_nim():
    positions = load_positions()
    paths = _default_curve_paths(scenario_defs={"Base": shocks.parallel(0)})
    summary, detail, cohort_detail = engine.run_scenario(positions, paths["Base"], scenario_label="Base")
    ftp_detail, _ = ftp_aggregate.compute_ftp_pnl(
        positions, detail, paths["Base"], config.STARTING_BENCHMARK_RATE, cohort_detail_df=cohort_detail
    )

    jv = joint_view.compute_joint_view(positions, detail, summary, ftp_detail, month=0)
    total_nim = summary.loc[summary["month"] == 0, "nim"].iloc[0]
    assert jv["nim_contribution"].sum() == pytest.approx(total_nim, abs=1e-9)


def test_removing_a_liability_changes_lcr_denominator_by_its_own_outflow_contribution():
    positions = load_positions()
    balances = {p.name: p.balance for p in positions}
    result_full = lcr.compute_lcr(positions, balances)

    target_name = "Savings & MMDA - Core"
    positions_without = [p for p in positions if p.name != target_name]
    balances_without = {k: v for k, v in balances.items() if k != target_name}
    result_without = lcr.compute_lcr(positions_without, balances_without)

    delta = result_full["net_outflows"] - result_without["net_outflows"]
    assert delta == pytest.approx(result_full["outflow_breakdown"][target_name])


# ---------------------------------------------------------------------------
# 4. AFS MTM: unrealized gains under -200bps, buffer rises during the ramp,
#    HTM never shows an MTM figure at all.
# ---------------------------------------------------------------------------

def test_afs_mtm_shows_gains_in_falling_rates_and_excludes_htm():
    positions = load_positions()
    paths = _default_curve_paths(scenario_defs={"-200 bps": shocks.parallel(-200)})
    _, detail, _ = engine.run_scenario(positions, paths["-200 bps"], scenario_label="-200 bps")
    mtm_detail, mtm_summary = mtm.compute_afs_mtm_report(positions, paths["-200 bps"], detail)

    assert "Agency MBS" not in mtm_detail["position"].unique()  # HTM, never revalued

    end_month = mtm_summary["month"].max()
    assert mtm_summary.loc[mtm_summary["month"] == end_month, "total_unrealized_gain"].iloc[0] > 0

    ramp = mtm_summary[mtm_summary["month"] <= 12].sort_values("month")
    assert ramp["total_unrealized_gain"].is_monotonic_increasing
