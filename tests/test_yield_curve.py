"""
Acceptance tests for the yield-curve term structure and rate-shock/scenario
machinery: interpolation/forward rates, curve shocks, and the scenario path
builder (regression against the pre-curve scalar benchmark-path convention).
Run with: py -m pytest tests/ -q
"""

import numpy as np
import pytest

from curve import shocks
from curve.yield_curve import YieldCurve
from curve.scenarios import build_curve_scenarios


# ---------------------------------------------------------------------------
# 1. YieldCurve interpolation / forward / extrapolation
# ---------------------------------------------------------------------------

def test_yield_curve_interpolation_and_extrapolation():
    curve = YieldCurve([1.0, 2.0, 5.0], [0.03, 0.04, 0.05])
    # interpolates linearly between tenor points
    assert curve.spot(1.5) == pytest.approx(0.035)
    # flat extrapolation beyond both ends
    assert curve.spot(0.1) == pytest.approx(0.03)
    assert curve.spot(20.0) == pytest.approx(0.05)


def test_yield_curve_forward_matches_implied_forward_formula():
    curve = YieldCurve([1.0, 2.0], [0.03, 0.04])
    fwd = curve.forward(1.0, 2.0)
    expected = ((1.04) ** 2 / (1.03) ** 1) ** (1 / (2 - 1)) - 1
    assert fwd == pytest.approx(expected, abs=1e-10)


# ---------------------------------------------------------------------------
# 2. Curve shocks
# ---------------------------------------------------------------------------

def test_parallel_shock_shifts_every_tenor_equally():
    curve = YieldCurve([0.25, 1.0, 5.0, 10.0], [0.03, 0.035, 0.04, 0.045])
    shifted = curve.shifted(shocks.parallel(100))
    for t in curve.tenors:
        assert shifted.spot(t) == pytest.approx(curve.spot(t) + 0.01, abs=1e-12)


def test_steepener_moves_long_end_more_than_short_end():
    curve = YieldCurve([0.25, 1.0, 5.0, 10.0], [0.03, 0.035, 0.04, 0.045])
    shift_fn = shocks.steepener(short_bps=-50, long_bps=100)
    shifted = curve.shifted(shift_fn)
    short_move = shifted.spot(0.25) - curve.spot(0.25)
    long_move = shifted.spot(10.0) - curve.spot(10.0)
    assert long_move > short_move


def test_shocked_rates_floor_at_zero():
    curve = YieldCurve([0.25, 1.0], [0.001, 0.002])
    shifted = curve.shifted(shocks.parallel(-200))
    assert shifted.spot(0.25) >= 0.0
    assert shifted.spot(1.0) >= 0.0


# ---------------------------------------------------------------------------
# 3. Regression: flat curve + parallel scenarios reproduce the old scalar path
# ---------------------------------------------------------------------------

def _old_build_benchmark_path(starting_rate, shock, horizon_months, ramp_months):
    """Reference implementation of the original scalar-benchmark path builder
    (curve/scenarios.py's predecessor, before the curve-first rebuild)."""
    path = np.empty(horizon_months)
    for m in range(horizon_months):
        ramp_fraction = min(1.0, (m + 1) / ramp_months) if ramp_months > 0 else 1.0
        path[m] = starting_rate + shock * ramp_fraction
    return np.clip(path, 0.0, None)


@pytest.mark.parametrize("shock_bps", [0, 100, 200, -100, -200])
def test_curve_scenarios_reproduce_old_scalar_benchmark_path(shock_bps):
    starting_rate = 0.0425
    flat_curve = YieldCurve([1 / 12, 1.0, 10.0], [starting_rate, starting_rate, starting_rate])
    scenario_defs = {"s": shocks.parallel(shock_bps)}
    paths = build_curve_scenarios(flat_curve, scenario_defs, horizon_months=24, ramp_months=12)
    got = paths["s"].short_rate_array()
    expected = _old_build_benchmark_path(starting_rate, shock_bps / 10000, 24, 12)
    np.testing.assert_allclose(got, expected, atol=1e-12)
