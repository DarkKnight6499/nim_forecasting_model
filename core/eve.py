"""
Full-revaluation Economic Value of Equity: EVE = PV(asset cashflows) -
PV(liability cashflows), replacing the old principal-only repricing-ladder
approximation with actual cashflow discounting (principal plus coupon
interest, via core/cashflows.py) under the shocked curve.

Each position's z-spread is solved once against the base (unshocked) curve
and held constant across every scenario - the point of a z-spread is to
isolate the curve's own movement from the position's credit/liquidity
margin, which shouldn't change just because a rate scenario is being
evaluated. Because every position reprices to exactly its book balance at
the base curve by construction, base EVE equals book equity (assets minus
liabilities) to within pennies - a sanity invariant this module's callers
can check, not a coincidence.

Administered (NMD) positions use their behavioral-duration replicating
ladder for principal timing (use_repricing_schedule=True in
core/cashflows.py), the same IRRBB convention already used by the gap
report and pooled_replicating FTP, not their liquidity-decay attrition
curve.

The linear duration approximation (model/alm_reports.py) is a first-order
estimate of the same quantity; full revaluation additionally captures
convexity (the price-yield relationship's curvature), which is why -200bps
and +200bps delta EVE aren't equal and opposite here the way the linear
approximation forces them to be.
"""

import pandas as pd

import config
from core import cashflows

EVE_MAX_MONTHS = 360


def solve_position_z_spreads(positions, curve, max_months=EVE_MAX_MONTHS):
    return {
        p.name: cashflows.solve_z_spread(p, curve, max_months, use_repricing_schedule=True)
        for p in positions
    }


def compute_position_pv(position, curve, z_spread, max_months=EVE_MAX_MONTHS):
    return cashflows.pv(position, curve, z_spread, max_months, use_repricing_schedule=True)


def compute_eve(positions, curve, z_spreads=None, max_months=EVE_MAX_MONTHS):
    """
    Returns (eve, asset_pv, liability_pv). z_spreads: optional
    {position.name: spread}; if None, solved fresh against `curve` for
    every position (so every position reprices to par at this exact curve).
    compute_eve_sensitivity solves once against the base curve and reuses
    the same spreads for every shocked curve instead - see module docstring
    for why that distinction matters.
    """
    if z_spreads is None:
        z_spreads = solve_position_z_spreads(positions, curve, max_months)
    asset_pv = sum(
        compute_position_pv(p, curve, z_spreads[p.name], max_months) for p in positions if p.side == "asset"
    )
    liability_pv = sum(
        compute_position_pv(p, curve, z_spreads[p.name], max_months) for p in positions if p.side == "liability"
    )
    return asset_pv - liability_pv, asset_pv, liability_pv


def compute_eve_sensitivity(positions, base_curve, shock_scenarios, total_equity=None, max_months=EVE_MAX_MONTHS):
    """
    shock_scenarios: {label: shift_fn(tenor_years) -> decimal}, same shape as
    curve/shocks.py builders. Returns a DataFrame: scenario, eve_full_reval,
    delta_eve_full_reval, delta_eve_pct_equity_full_reval,
    base_eve_vs_book_equity_error (repeated per row: base EVE minus book
    equity, a sanity check that should be within pennies of zero).
    """
    total_assets = sum(p.balance for p in positions if p.side == "asset")
    total_liab = sum(p.balance for p in positions if p.side == "liability")
    equity = total_equity if total_equity else (total_assets - total_liab)
    equity = equity if equity and equity > 0 else config.EQUITY_CAPITAL_FALLBACK

    z_spreads = solve_position_z_spreads(positions, base_curve, max_months)
    base_eve, _, _ = compute_eve(positions, base_curve, z_spreads, max_months)
    base_eve_vs_book_equity_error = base_eve - (total_assets - total_liab)

    rows = []
    for label, shift_fn in shock_scenarios.items():
        shocked_curve = base_curve.shifted(shift_fn)
        shocked_eve, _, _ = compute_eve(positions, shocked_curve, z_spreads, max_months)
        delta_eve = shocked_eve - base_eve
        rows.append({
            "scenario": label, "eve_full_reval": shocked_eve,
            "delta_eve_full_reval": delta_eve, "delta_eve_pct_equity_full_reval": delta_eve / equity,
            "base_eve_vs_book_equity_error": base_eve_vs_book_equity_error,
        })
    return pd.DataFrame(rows)
