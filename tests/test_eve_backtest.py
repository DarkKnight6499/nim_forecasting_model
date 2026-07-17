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
from core import engine, eve, backtest


# ---------------------------------------------------------------------------
# 1. Convexity: full-reval |delta EVE| under -200bps exceeds +200bps (the
#    asset book's long duration dominates), unlike the linear approximation
#    which is symmetric by construction. z-spreads are solved once against
#    the base curve and reused for every shocked curve (see core/eve.py):
#    solving fresh against each shocked curve would reprice every position
#    back to par every time and hide the convexity entirely.
# ---------------------------------------------------------------------------

def test_full_reval_eve_shows_positive_convexity_vs_linear_approximation():
    positions = load_positions()
    base_curve = YieldCurve(config.FALLBACK_CURVE_TENORS, config.FALLBACK_CURVE_RATES)

    z_spreads = eve.solve_position_z_spreads(positions, base_curve)
    base_eve, _, _ = eve.compute_eve(positions, base_curve, z_spreads)
    up_eve, _, _ = eve.compute_eve(positions, base_curve.shifted(shocks.parallel(200)), z_spreads)
    down_eve, _, _ = eve.compute_eve(positions, base_curve.shifted(shocks.parallel(-200)), z_spreads)

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
# 2. Back-test: attribution sums exactly to total error; the model run
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
