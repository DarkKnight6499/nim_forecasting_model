"""
Acceptance tests for the yield-curve / cashflow-first balance sheet rebuild.
Run with: py -m pytest tests/ -q
"""

import subprocess
import sys

import numpy as np
import pytest

import config
from curve import shocks
from curve.yield_curve import YieldCurve
from curve.scenarios import build_curve_scenarios
from core.balance_sheet import load_positions
from core import engine
from model import alm_reports


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
    """Reference implementation of the pre-Phase-0 scalar path builder (curve/scenarios.py predecessor)."""
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


# ---------------------------------------------------------------------------
# 4. balance_sheet.yaml reproduces the old DEFAULT_BUCKETS totals
# ---------------------------------------------------------------------------

def test_balance_sheet_yaml_matches_old_default_bucket_totals():
    positions = load_positions()
    total_assets = sum(p.balance for p in positions if p.side == "asset")
    total_liab = sum(p.balance for p in positions if p.side == "liability")
    # Reference totals from the pre-Phase-0 config.DEFAULT_BUCKETS (see git history).
    assert total_assets == pytest.approx(4_700_000_000)
    assert total_liab == pytest.approx(3_300_000_000)


# ---------------------------------------------------------------------------
# 5. Repricing gap: administered (CASA) balances no longer point-mass in 0-3M
# ---------------------------------------------------------------------------

def test_administered_positions_no_longer_point_mass_in_gap_report():
    positions = load_positions()
    casa = [p for p in positions if p.category_type == "administered"]
    total_casa = sum(p.balance for p in casa)

    # Isolate just the administered (CASA) positions' contribution to the near-term
    # bands - not the whole book's RSL, which also includes laddered CDs and
    # variable short-term borrowings that legitimately belong in 0-3M.
    near_term_casa = 0.0
    for p in casa:
        schedule, _ = p.repricing_schedule(config.ALM_MAX_MONTHS)
        near_term_casa += schedule[0:3].sum()

    # Every CASA position here has lag_months <= 3, so the old lag_months-point-mass
    # convention would have dumped the ENTIRE CASA balance into the 0-3M bands.
    # Under the behavioral-duration ladder, only a small fraction should land there.
    assert near_term_casa < 0.25 * total_casa


# ---------------------------------------------------------------------------
# 6. Balance sheet identity holds every month from month 1 onward
# ---------------------------------------------------------------------------

def test_balance_sheet_identity_holds_each_month():
    positions = load_positions()
    flat_curve = YieldCurve([1 / 12, 1.0, 10.0], [0.0425, 0.0425, 0.0425])
    paths = build_curve_scenarios(flat_curve, {"Base": shocks.parallel(0)}, horizon_months=24, ramp_months=12)

    summary_df, detail_df, _ = engine.run_scenario(positions, paths["Base"], scenario_label="Base")

    for month in range(1, 24):
        month_detail = detail_df[detail_df["month"] == month]
        assets = month_detail.loc[month_detail["side"] == "asset", "balance"].sum()
        liabs = month_detail.loc[month_detail["side"] == "liability", "balance"].sum()
        equity = summary_df.loc[summary_df["month"] == month, "equity"].iloc[0]
        assert abs(assets - liabs - equity) < 1.0, f"identity violated at month {month}"


# ---------------------------------------------------------------------------
# 7. End-to-end CLI still runs, and PNC day-0 NIM matches reported within 10bps
# ---------------------------------------------------------------------------

def test_main_runs_end_to_end_synthetic():
    result = subprocess.run([sys.executable, "main.py"], capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr


def test_main_runs_end_to_end_pnc_and_matches_reported_nim():
    result = subprocess.run(
        [sys.executable, "main.py", "--bank-cert", "6384"],
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stderr
    output = result.stdout

    reported_line = next(line for line in output.splitlines() if "Bank's own latest reported NIM" in line)
    reported_pct = float(reported_line.split(":")[1].strip().split("%")[0])

    month0_line = next(line for line in output.splitlines() if line.startswith("Month  0"))
    base_field = next(f for f in month0_line.split() if f.startswith("Base") is False and "=" in f and month0_line)
    # "Base (flat)=X.XX%" is the last "label=value" token on the line
    base_token = [tok for tok in month0_line.replace("Base (flat)", "Base(flat)").split() if tok.startswith("Base(flat)")][0]
    model_pct = float(base_token.split("=")[1].rstrip("%"))

    assert abs(model_pct - reported_pct) < 0.10  # within 10bps
