"""
Acceptance tests for pluggable FTP methods (core/ftp/): per-position method
selection, reconciliation to total NII regardless of the method mix, and the
pooled-replicating ladder's smoothing behavior vs. matched-maturity.
Run with: py -m pytest tests/ -q
"""

import subprocess
import sys

import pytest

import config
from curve import shocks
from curve.yield_curve import YieldCurve
from curve.scenarios import build_curve_scenarios
from core.balance_sheet import load_positions
from core.position import Position
from core import engine
from core.ftp import aggregate as ftp_aggregate
from core.ftp.registry import build_rate_series


def _default_curve_paths(months=24, ramp=12, scenario_defs=None):
    base_curve = YieldCurve(config.FALLBACK_CURVE_TENORS, config.FALLBACK_CURVE_RATES)
    scenario_defs = scenario_defs or {"+200 bps": shocks.parallel(200)}
    return build_curve_scenarios(base_curve, scenario_defs, months, ramp)


# ---------------------------------------------------------------------------
# 1. Default balance sheet: administered positions use pooled_replicating,
#    everything else defaults to matched_maturity.
# ---------------------------------------------------------------------------

def test_administered_positions_default_to_pooled_replicating():
    positions = load_positions()
    for p in positions:
        if p.category_type == "administered":
            assert p.ftp_method == "pooled_replicating"
        else:
            assert p.ftp_method == "matched_maturity"


# ---------------------------------------------------------------------------
# 2. Mixed ftp_method book still reconciles: customer margin + ALM desk P&L
#    sums to total NII every month, regardless of which methods are mixed.
# ---------------------------------------------------------------------------

def test_ftp_reconciles_to_total_nii_with_mixed_methods():
    positions = load_positions()
    paths = _default_curve_paths()
    _, detail = engine.run_scenario(positions, paths["+200 bps"], scenario_label="+200 bps")
    _, monthly = ftp_aggregate.compute_ftp_pnl(positions, detail, paths["+200 bps"], config.STARTING_BENCHMARK_RATE)
    assert monthly["identity_check"].abs().max() < 1e-6


# ---------------------------------------------------------------------------
# 3. Swapping a position's ftp_method actually changes its FTP rate.
# ---------------------------------------------------------------------------

def test_swapping_ftp_method_changes_rate():
    position = Position(
        name="Test NMD", side="liability", category_type="administered",
        balance=100_000_000, rate=0.02, index="ADMIN", beta=0.3, lag_months=2,
        behavioral_duration_years=2.5,
    )
    curve0 = YieldCurve([1 / 12, 1.0, 5.0, 10.0], [0.03, 0.035, 0.045, 0.05])
    paths = build_curve_scenarios(curve0, {"+200 bps": shocks.parallel(200)}, horizon_months=24, ramp_months=12)
    path = paths["+200 bps"]

    position.ftp_method = "matched_maturity"
    matched = build_rate_series(position, path, 0.03)
    position.ftp_method = "pooled_replicating"
    pooled = build_rate_series(position, path, 0.03)
    position.ftp_method = "straight_spread"
    straight = build_rate_series(position, path, 0.03)

    assert matched != pooled
    assert pooled != straight
    assert matched != straight


# ---------------------------------------------------------------------------
# 4. Pooled-replicating smooths the transition of a rate shock more than
#    matched-maturity's single fixed-tenor lookup (the whole point of using a
#    rolling ladder instead of a point-in-time curve read).
# ---------------------------------------------------------------------------

def test_pooled_replicating_smooths_more_than_matched_maturity():
    position = Position(
        name="Test NMD", side="liability", category_type="administered",
        balance=100_000_000, rate=0.02, index="ADMIN", beta=0.3, lag_months=2,
        behavioral_duration_years=2.5,
    )
    curve0 = YieldCurve([1 / 12, 1.0, 5.0, 10.0], [0.03, 0.035, 0.045, 0.05])
    paths = build_curve_scenarios(curve0, {"+200 bps": shocks.parallel(200)}, horizon_months=24, ramp_months=12)
    path = paths["+200 bps"]

    position.ftp_method = "matched_maturity"
    matched = build_rate_series(position, path, 0.03)
    position.ftp_method = "pooled_replicating"
    pooled = build_rate_series(position, path, 0.03)

    # Month-by-month move in matched_maturity fully tracks the curve; pooled's
    # ladder rolls only a fraction of its notional each month, so its month-1
    # move should be smaller than matched_maturity's.
    assert abs(pooled[1] - pooled[0]) < abs(matched[1] - matched[0])


# ---------------------------------------------------------------------------
# 5. Full suite still green end to end with the new FTP package.
# ---------------------------------------------------------------------------

def test_main_runs_end_to_end_pnc_with_ftp_package():
    result = subprocess.run(
        [sys.executable, "main.py", "--bank-cert", "6384"],
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert "ALM Desk P&L stability" in result.stdout
