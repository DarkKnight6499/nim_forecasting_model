"""
Acceptance tests for the cohort-based, index-driven repricing engine
(staggered variable resets, the MCLR liability-cost chain, curve-tenor
pricing for new production, and rate-dependent CPR burnout).
Run with: py -m pytest tests/ -q
"""

import pytest

import config
from curve import shocks
from curve.yield_curve import YieldCurve
from curve.scenarios import build_curve_scenarios
from core.balance_sheet import load_positions
from core.position import Position
from core.indices import index_rate
from core import engine
from core.engine import _seed_cohorts, _step_fixed_amortizing_cohorts


def _run(positions, curve_path, label="s"):
    return engine.run_scenario(positions, curve_path, scenario_label=label)


def _default_curve_paths(months=24, ramp=12, scenario_defs=None):
    base_curve = YieldCurve(config.FALLBACK_CURVE_TENORS, config.FALLBACK_CURVE_RATES)
    scenario_defs = scenario_defs or config.RATE_SCENARIOS
    return build_curve_scenarios(base_curve, scenario_defs, months, ramp)


# ---------------------------------------------------------------------------
# 1. No sawtooth: staggered resets smooth out the month-over-month NIM series
# ---------------------------------------------------------------------------

def test_no_sawtooth_in_nim_series_under_plus_200bps():
    positions = load_positions()
    paths = _default_curve_paths(scenario_defs={"+200 bps": shocks.parallel(200)})
    summary_df, _, _ = _run(positions, paths["+200 bps"], label="+200 bps")

    nim = summary_df.sort_values("month")["nim"].to_numpy()
    window = nim[3:13]  # months 3-12 inclusive
    deltas = window[1:] - window[:-1]
    signs = [1 if d > 0 else -1 for d in deltas if d != 0]
    flips = sum(1 for a, b in zip(signs, signs[1:]) if a != b)
    assert flips <= 2, f"expected at most 2 sign flips, got {flips}"


# ---------------------------------------------------------------------------
# 2. Staggered 3-month reset book: seeded into 3 roughly equal cohorts
# ---------------------------------------------------------------------------

def test_staggered_reset_seeds_equal_cohorts():
    positions = load_positions()
    ci = next(p for p in positions if p.name == "C&I loans (variable)")
    assert ci.reset_frequency_months == 3

    curve0 = YieldCurve(config.FALLBACK_CURVE_TENORS, config.FALLBACK_CURVE_RATES)
    cohorts = _seed_cohorts(ci, curve0)

    assert len(cohorts) == 3
    for c in cohorts:
        assert c.balance == pytest.approx(ci.balance / 3, rel=1e-9)
    assert {c.phase for c in cohorts} == {0, 1, 2}


# ---------------------------------------------------------------------------
# 3. MCLR chain: asset yields respond to the model's own liability cost
# ---------------------------------------------------------------------------

def test_mclr_linked_asset_yield_tracks_deposit_and_borrowing_cost():
    positions = load_positions()
    paths = _default_curve_paths(scenario_defs={
        "+200 bps": shocks.parallel(200),
        "-200 bps": shocks.parallel(-200),
    })

    _, detail_up, _ = _run(positions, paths["+200 bps"], label="+200 bps")
    _, detail_down, _ = _run(positions, paths["-200 bps"], label="-200 bps")

    def ci_rate(detail_df, month):
        row = detail_df[(detail_df["bucket"] == "C&I loans (variable)") & (detail_df["month"] == month)]
        return row["rate"].iloc[0]

    # Month 3 is the first month by which every staggered cohort has completed its
    # first reset onto MCLR-based pricing (a one-time transition off the pre-MCLR
    # day-0 anchor rate) - compare *from there*, not from month 0, to isolate the
    # ongoing rate cycle's effect from that one-time regime-transition step.
    rate_up_m3 = ci_rate(detail_up, 3)
    rate_up_m12 = ci_rate(detail_up, 12)
    rate_down_m12 = ci_rate(detail_down, 12)

    # Rising rates -> rising deposit/borrowing cost -> rising MCLR-linked yield.
    assert rate_up_m12 > rate_up_m3
    # A falling-rate cycle should leave the MCLR-linked yield lower than a rising one.
    assert rate_down_m12 < rate_up_m12


# ---------------------------------------------------------------------------
# 4. New fixed production prices off its own tenor, not the short end
# ---------------------------------------------------------------------------

def test_new_production_prices_off_long_end_in_steepener():
    curve0 = YieldCurve([1 / 12, 1.0, 5.0, 10.0], [0.03, 0.035, 0.04, 0.045])
    steepener = curve0.shifted(shocks.steepener(short_bps=-50, long_bps=100))

    short_move = index_rate("SHORT", steepener) - index_rate("SHORT", curve0)
    five_yr_move = index_rate("TENOR", steepener, tenor_years=5.0) - index_rate("TENOR", curve0, tenor_years=5.0)

    assert five_yr_move > short_move


# ---------------------------------------------------------------------------
# 5. Burnout: rate-dependent CPR runs off high-coupon vintages faster
# ---------------------------------------------------------------------------

def _simulate_original_cohort_survival(refi_sensitivity, curve_path):
    position = Position(
        name="Test mortgage", side="asset", category_type="fixed_amortizing",
        balance=100_000_000, rate=0.065, index="FIXED", origination_tenor_years=30,
        spread=0.015, cpr_annual=0.10, refi_sensitivity=refi_sensitivity, cpr_max=0.40,
        growth_rate_annual=0.0,
    )
    cohorts = _seed_cohorts(position, curve_path.curves[0])
    for t in range(1, len(curve_path.curves)):
        _step_fixed_amortizing_cohorts(position, cohorts, curve_path.curves[t], t)
    original = next((c for c in cohorts if c.origination_month == 0), None)
    return original.balance if original else 0.0


def test_burnout_speeds_up_runoff_when_rates_fall():
    base_curve = YieldCurve([1 / 12, 1.0, 30.0], [0.045, 0.045, 0.055])
    paths = build_curve_scenarios(base_curve, {
        "Base": shocks.parallel(0), "-200 bps": shocks.parallel(-200),
    }, horizon_months=24, ramp_months=12)

    survival_base_with_refi = _simulate_original_cohort_survival(2.5, paths["Base"])
    survival_down_with_refi = _simulate_original_cohort_survival(2.5, paths["-200 bps"])
    survival_base_no_refi = _simulate_original_cohort_survival(0.0, paths["Base"])
    survival_down_no_refi = _simulate_original_cohort_survival(0.0, paths["-200 bps"])

    # With refi sensitivity on, a falling-rate cycle should erode the original
    # (high-coupon) cohort faster than the flat base case.
    assert survival_down_with_refi < survival_base_with_refi
    # With refi sensitivity off, CPR is a flat constant - both scenarios erode identically.
    assert survival_down_no_refi == pytest.approx(survival_base_no_refi, rel=1e-9)
