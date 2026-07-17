"""
Acceptance tests for origination-locked FTP (core/ftp/matched_maturity.py),
the historical-cycle library, and the FTP policy spread calibrator.
Run with: py -m pytest tests/ -q
"""

import config
from curve import shocks
from curve.yield_curve import YieldCurve
from curve.scenarios import build_curve_scenarios
from curve.historical_cycles import HISTORICAL_CYCLES
from core.balance_sheet import load_positions
from core.position import Position
from core import engine
from core.engine import _seed_cohorts, _step_fixed_amortizing_cohorts
from core.ftp import aggregate as ftp_aggregate
from core.ftp.registry import build_rate_series
from core.ftp.spread_curve import spread_for_tenor
from core import ftp_calibration

import pytest


def _default_curve_paths(months=24, ramp=12, scenario_defs=None):
    base_curve = YieldCurve(config.FALLBACK_CURVE_TENORS, config.FALLBACK_CURVE_RATES)
    scenario_defs = scenario_defs or config.RATE_SCENARIOS
    return build_curve_scenarios(base_curve, scenario_defs, months, ramp)


# ---------------------------------------------------------------------------
# 1. Immunization: a fixed_amortizing cohort's locked coupon and locked FTP
#    rate both read the same origination-month curve, so its customer margin
#    is scenario-invariant even though the curve level at origination isn't.
# ---------------------------------------------------------------------------

def test_fixed_amortizing_cohort_margin_immunized_across_scenarios():
    base_curve = YieldCurve([1 / 12, 1.0, 5.0, 10.0], [0.03, 0.035, 0.04, 0.045])
    paths = build_curve_scenarios(base_curve, {"Base": shocks.parallel(0), "+200 bps": shocks.parallel(200)},
                                   horizon_months=24, ramp_months=12)

    def month3_cohort_margin_rate(label):
        position = Position(
            name="Test 5y loan", side="asset", category_type="fixed_amortizing",
            balance=100_000_000, rate=0.06, index="TENOR", origination_tenor_years=5.0,
            spread=0.02, cpr_annual=0.10, refi_sensitivity=0.0, cpr_max=0.10,
            growth_rate_annual=0.05,
        )
        curve_path = paths[label]
        cohorts = _seed_cohorts(position, curve_path.curves[0])
        for t in range(1, len(curve_path.curves)):
            _step_fixed_amortizing_cohorts(position, cohorts, curve_path.curves[t], t)

        cohort = next(c for c in cohorts if c.origination_month == 3)
        ftp_rate = curve_path.curves[3].spot(position.origination_tenor_years) + spread_for_tenor(position.origination_tenor_years)
        return cohort.rate - ftp_rate

    margin_base = month3_cohort_margin_rate("Base")
    margin_up = month3_cohort_margin_rate("+200 bps")
    assert margin_base == pytest.approx(margin_up, abs=1e-9)


# ---------------------------------------------------------------------------
# 2. Floater re-fix: a 3M-reset position's FTP rate changes only on reset
#    months and equals the then-current 3M curve point plus policy spread.
# ---------------------------------------------------------------------------

def test_variable_ftp_rate_only_moves_on_reset_months():
    position = Position(
        name="Test C&I", side="asset", category_type="variable",
        balance=100_000_000, rate=0.06, index="MCLR", spread=0.02,
        reset_frequency_months=3,
    )
    base_curve = YieldCurve([1 / 12, 0.25, 1.0, 10.0], [0.03, 0.032, 0.035, 0.045])
    paths = build_curve_scenarios(base_curve, {"+100 bps": shocks.parallel(100)}, horizon_months=13, ramp_months=6)
    path = paths["+100 bps"]

    rates = build_rate_series(position, path, config.STARTING_BENCHMARK_RATE)

    n = 3
    tenor_years = n / 12
    spread = spread_for_tenor(tenor_years)
    for t in range(len(rates)):
        last_reset_month = t - (t % n)
        expected = path.curves[last_reset_month].spot(tenor_years) + spread
        assert rates[t] == pytest.approx(expected)
        if t % n != 0:
            assert rates[t] == pytest.approx(rates[t - 1])


# ---------------------------------------------------------------------------
# 3. Reconciliation: max abs identity error stays under $0.01 across every
#    month of every default rate scenario (mixed ftp_method book).
# ---------------------------------------------------------------------------

def test_ftp_reconciles_across_all_default_scenarios_and_months():
    positions = load_positions()
    paths = _default_curve_paths()
    max_err = 0.0
    for label, path in paths.items():
        _, detail_df, cohort_detail_df = engine.run_scenario(positions, path, scenario_label=label)
        _, monthly = ftp_aggregate.compute_ftp_pnl(
            positions, detail_df, path, config.STARTING_BENCHMARK_RATE, cohort_detail_df=cohort_detail_df
        )
        max_err = max(max_err, monthly["identity_check"].abs().max())
    assert max_err < 0.01


# ---------------------------------------------------------------------------
# 4. Calibration: optimized spreads reduce cross-cycle ALM desk P&L variance
#    vs the static policy curve, and never breach the short-tenor floor.
# ---------------------------------------------------------------------------

def test_calibration_reduces_cross_cycle_alm_pnl_variance():
    positions = load_positions()
    base_curve = YieldCurve(config.FALLBACK_CURVE_TENORS, config.FALLBACK_CURVE_RATES)
    cycle_paths = {name: builder(base_curve, horizon_months=12) for name, builder in HISTORICAL_CYCLES.items()}

    before = ftp_calibration.cross_cycle_variance(positions, cycle_paths, config.STARTING_BENCHMARK_RATE)
    calibrated = ftp_calibration.calibrate_policy_spreads(positions, cycle_paths, config.STARTING_BENCHMARK_RATE)
    after = ftp_calibration.cross_cycle_variance(
        positions, cycle_paths, config.STARTING_BENCHMARK_RATE, spreads_by_tenor=calibrated
    )

    assert after < before
    for tenor, spread in calibrated.items():
        if tenor < config.FTP_SHORT_TENOR_CUTOFF_YEARS:
            assert spread >= config.FTP_SHORT_TENOR_MIN_SPREAD - 1e-9
