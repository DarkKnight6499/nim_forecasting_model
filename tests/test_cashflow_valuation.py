"""
Acceptance tests for the unified cashflow engine and z-spread valuation
(core/cashflows.py), and the EVE (core/eve.py) and AFS MTM (core/mtm.py)
rewrites that consume it.

Before this phase, EVE discounted principal-only repricing flows (no
coupons at all, so its level was not economically meaningful), and AFS MTM
calibrated away its own structural mispricing with a day-0 book/PV ratio
instead of solving for an economically meaningful spread. This module
verifies the replacement: one canonical coupon-bearing cashflow generator,
a solved z-spread that reprices every position to book at the base curve,
and the new "base EVE equals book equity" invariant that unlocks.

Run with: py -m pytest tests/ -q
"""

import pytest

import config
from curve import shocks
from curve.yield_curve import YieldCurve
from curve.scenarios import build_curve_scenarios
from core.balance_sheet import load_positions
from core.position import Position
from core import cashflows, engine, eve, mtm


# ---------------------------------------------------------------------------
# 1. A laddered position whose coupon equals the flat discount curve prices
#    close to par with z-spread 0 (no calibration factor anywhere).
# ---------------------------------------------------------------------------

def test_par_coupon_position_prices_close_to_par_with_zero_z_spread():
    curve = YieldCurve([1 / 12, 1.0, 5.0, 10.0], [0.04, 0.04, 0.04, 0.04])
    position = Position(
        name="Par Bond", side="asset", category_type="laddered",
        balance=100_000_000, rate=0.04, index="TENOR", origination_tenor_years=5,
        ladder_months=60,
    )
    price = cashflows.pv(position, curve, z_spread=0.0)

    # Coupon accrual is a simple monthly rate (rate/12); the curve's discount
    # factor compounds annually to a fractional-year power (YieldCurve.df's
    # convention, used everywhere in this codebase) - two different
    # day-count conventions, so "coupon equals yield" doesn't give an exact
    # par identity here, just a close one (a few bps), unlike a true bullet
    # bond under matching conventions. 5bp is generous against that residual.
    five_bp_of_value = position.balance * 0.0005
    assert price == pytest.approx(position.balance, abs=five_bp_of_value)


# ---------------------------------------------------------------------------
# 2. solve_z_spread reprices every position in the default book to its book
#    balance within $0.01; a position with a known coupon-over-curve spread
#    solves close to that spread.
# ---------------------------------------------------------------------------

def test_solve_z_spread_reprices_every_default_position_to_book_balance():
    positions = load_positions()
    base_curve = YieldCurve(config.FALLBACK_CURVE_TENORS, config.FALLBACK_CURVE_RATES)

    for p in positions:
        z = cashflows.solve_z_spread(p, base_curve, use_repricing_schedule=True)
        price = cashflows.pv(p, base_curve, z, use_repricing_schedule=True)
        assert price == pytest.approx(p.balance, abs=0.01), f"{p.name} did not reprice to book"


def test_solve_z_spread_recovers_a_known_coupon_spread_approximately():
    curve = YieldCurve([1 / 12, 1.0, 5.0, 10.0], [0.04, 0.04, 0.04, 0.04])
    position = Position(
        name="150bp Spread Bond", side="asset", category_type="laddered",
        balance=100_000_000, rate=0.055, index="TENOR", origination_tenor_years=5,
        ladder_months=60,
    )
    z = cashflows.solve_z_spread(position, curve)
    assert abs(z - 0.015) < 0.0025  # within 25bps of the 150bp coupon-over-curve differential


# ---------------------------------------------------------------------------
# 3. Base EVE equals book equity on the default book at the base curve
#    (every position reprices to par there by construction).
# ---------------------------------------------------------------------------

def test_base_eve_equals_book_equity_on_default_book():
    positions = load_positions()
    base_curve = YieldCurve(config.FALLBACK_CURVE_TENORS, config.FALLBACK_CURVE_RATES)

    total_assets = sum(p.balance for p in positions if p.side == "asset")
    total_liab = sum(p.balance for p in positions if p.side == "liability")
    base_eve, _, _ = eve.compute_eve(positions, base_curve)

    assert base_eve == pytest.approx(total_assets - total_liab, abs=1.0)


# ---------------------------------------------------------------------------
# 4. A pure monthly-floater book shows negligible delta EVE under a parallel
#    shock: floaters carry no duration beyond their next reset.
# ---------------------------------------------------------------------------

def test_pure_floater_book_shows_negligible_delta_eve_under_parallel_shock():
    base_curve = YieldCurve(config.FALLBACK_CURVE_TENORS, config.FALLBACK_CURVE_RATES)
    floater_book = [
        Position(name="Floater Asset", side="asset", category_type="variable",
                  balance=100_000_000, rate=0.04, index="SHORT"),
        Position(name="Floater Liab", side="liability", category_type="variable",
                  balance=50_000_000, rate=0.02, index="SHORT"),
    ]
    z_spreads = eve.solve_position_z_spreads(floater_book, base_curve)
    base_eve, _, _ = eve.compute_eve(floater_book, base_curve, z_spreads)
    shocked_eve, _, _ = eve.compute_eve(floater_book, base_curve.shifted(shocks.parallel(200)), z_spreads)

    total_balance = sum(p.balance for p in floater_book)
    assert abs(shocked_eve - base_eve) < 0.001 * total_balance  # within 10bps of notional


# ---------------------------------------------------------------------------
# 5. AFS MTM: month-0 unrealized gain is close to $0 for every AFS position
#    (the z-spread calibration point); -200bps gains are positive; HTM never
#    appears in the MTM detail.
# ---------------------------------------------------------------------------

def test_afs_mtm_month0_gain_near_zero_and_shows_gains_under_falling_rates():
    positions = load_positions()
    base_curve = YieldCurve(config.FALLBACK_CURVE_TENORS, config.FALLBACK_CURVE_RATES)
    paths = build_curve_scenarios(base_curve, {"-200 bps": shocks.parallel(-200)}, horizon_months=24, ramp_months=12)

    _, detail, _ = engine.run_scenario(positions, paths["-200 bps"], scenario_label="-200 bps")
    mtm_detail, mtm_summary = mtm.compute_afs_mtm_report(positions, paths["-200 bps"], detail)

    month0 = mtm_detail[mtm_detail["month"] == 0]
    assert (month0["unrealized_gain"].abs() < 0.01).all()

    assert "Agency MBS" not in mtm_detail["position"].unique()  # HTM, never revalued

    end_month = mtm_summary["month"].max()
    assert mtm_summary.loc[mtm_summary["month"] == end_month, "total_unrealized_gain"].iloc[0] > 0
