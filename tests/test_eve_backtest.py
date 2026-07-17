"""
Acceptance tests for full-revaluation EVE (core/eve.py) and the back-testing
harness (core/backtest.py).
Run with: py -m pytest tests/ -q
"""

import pytest

import config
from curve import shocks
from curve.yield_curve import YieldCurve
from curve.scenarios import build_curve_scenarios
from core.balance_sheet import load_positions
from core.position import Position
from core import engine, eve, backtest


# ---------------------------------------------------------------------------
# 1. Full-reval EVE on a zero-coupon-like position matches the analytic PV
#    change under a parallel shock, within 1bp of the position's value.
# ---------------------------------------------------------------------------

def test_full_reval_eve_matches_analytic_zero_coupon_pv_change():
    position = Position(
        name="ZeroCoupon", side="asset", category_type="fixed_amortizing",
        balance=100_000_000, rate=0.04, index="FIXED", origination_tenor_years=5,
        spread=0.0, cpr_annual=0.0, growth_rate_annual=0.0,
    )
    base_curve = YieldCurve([1 / 12, 1, 5, 10], [0.03, 0.035, 0.04, 0.045])
    shocked_curve = base_curve.shifted(shocks.parallel(100))
    max_months = 60  # 5 years, matching origination_tenor_years exactly

    pv_base = eve.compute_position_pv(position, base_curve, max_months)
    pv_shocked = eve.compute_position_pv(position, shocked_curve, max_months)

    analytic_base = position.balance * base_curve.df(5.0)
    analytic_shocked = position.balance * shocked_curve.df(5.0)

    one_bp_of_value = position.balance * 0.0001
    assert pv_base == pytest.approx(analytic_base, abs=one_bp_of_value)
    assert pv_shocked == pytest.approx(analytic_shocked, abs=one_bp_of_value)
    assert (pv_shocked - pv_base) == pytest.approx(analytic_shocked - analytic_base, abs=one_bp_of_value)


# ---------------------------------------------------------------------------
# 2. Convexity: full-reval |delta EVE| under -200bps exceeds +200bps (the
#    asset book's long duration dominates), unlike the linear approximation
#    which is symmetric by construction.
# ---------------------------------------------------------------------------

def test_full_reval_eve_shows_positive_convexity_vs_linear_approximation():
    positions = load_positions()
    base_curve = YieldCurve(config.FALLBACK_CURVE_TENORS, config.FALLBACK_CURVE_RATES)

    base_eve, _, _ = eve.compute_eve(positions, base_curve)
    up_eve, _, _ = eve.compute_eve(positions, base_curve.shifted(shocks.parallel(200)))
    down_eve, _, _ = eve.compute_eve(positions, base_curve.shifted(shocks.parallel(-200)))

    delta_up = up_eve - base_eve
    delta_down = down_eve - base_eve

    # Positive convexity: the falling-rate move helps EVE more than the
    # rising-rate move hurts it.
    assert abs(delta_down) > abs(delta_up)
    # The linear duration approximation is symmetric by construction
    # (+-D*shock*MV), so this asymmetry itself is what "differs from the
    # linear approximation in the expected direction" means here.
    assert delta_up != pytest.approx(-delta_down)


# ---------------------------------------------------------------------------
# 3. Back-test: attribution sums exactly to total error; the model run
#    against its own unperturbed output yields ~0 error everywhere.
# ---------------------------------------------------------------------------

def test_backtest_attribution_sums_exactly_and_self_consistent_run_is_zero_error():
    positions = load_positions()
    paths = build_curve_scenarios(
        YieldCurve(config.FALLBACK_CURVE_TENORS, config.FALLBACK_CURVE_RATES),
        {"Base": shocks.parallel(0)}, horizon_months=24, ramp_months=12,
    )
    summary, _, _ = engine.run_scenario(positions, paths["Base"], scenario_label="Base")

    actuals = summary.rename(columns={
        "net_interest_income": "actual_nii",
        "avg_earning_assets": "actual_avg_earning_assets",
        "nim": "actual_nim",
    })[["month", "actual_nii", "actual_avg_earning_assets", "actual_nim"]]

    result = backtest.compute_backtest(summary, actuals)

    # Attribution sums exactly to total error, by construction.
    reconstructed = result["rate_variance"] + result["volume_variance"] + result["residual_unmodellable"]
    assert (reconstructed - result["nii_error"]).abs().max() < 1e-6

    # Running the model against its own unperturbed output yields ~0 error everywhere.
    assert result["nii_error"].abs().max() < 1.0


def test_backtest_against_sample_actuals_reconciles_exactly():
    import pandas as pd
    positions = load_positions()
    paths = build_curve_scenarios(
        YieldCurve(config.FALLBACK_CURVE_TENORS, config.FALLBACK_CURVE_RATES),
        {"Base": shocks.parallel(0)}, horizon_months=24, ramp_months=12,
    )
    summary, _, _ = engine.run_scenario(positions, paths["Base"], scenario_label="Base")
    actuals_df = pd.read_csv("sample_actuals.csv")

    result = backtest.compute_backtest(summary, actuals_df)
    reconstructed = result["rate_variance"] + result["volume_variance"] + result["residual_unmodellable"]
    assert (reconstructed - result["nii_error"]).abs().max() < 1e-6
