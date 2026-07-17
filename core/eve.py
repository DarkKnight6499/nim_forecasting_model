"""
Full-revaluation Economic Value of Equity: EVE = PV(asset cashflows) -
PV(liability cashflows), replacing model/alm_reports.py's linear duration
approximation with actual cashflow discounting under the shocked curve.

Cashflow timing comes from Position.repricing_schedule extended to full
runoff (max_months=360): for administered (NMD) positions this is the
behavioral-duration replicating ladder (the same convention used for the
gap report and pooled_replicating FTP), not the liquidity_decay_annual
attrition curve cashflow_schedule() would give - EVE conventionally treats
NMDs as the ladder for rate-risk purposes, distinct from actual withdrawal
timing. Every other category_type's repricing_schedule already matches its
real cashflow timing.

The linear duration approximation is a first-order (delta) estimate of the
same quantity; full revaluation additionally captures convexity (the
price-yield relationship's curvature), which is why -200bps and +200bps
delta EVE aren't equal and opposite here the way the linear approximation
forces them to be.
"""

import pandas as pd

import config

EVE_MAX_MONTHS = 360


def compute_position_pv(position, curve, max_months=EVE_MAX_MONTHS):
    schedule, leftover = position.repricing_schedule(max_months)
    pv = 0.0
    for t, cashflow in enumerate(schedule):
        if cashflow > 0:
            pv += cashflow * curve.df((t + 1) / 12)
    if leftover > 0:
        pv += leftover * curve.df(max_months / 12)
    return pv


def compute_eve(positions, curve, max_months=EVE_MAX_MONTHS):
    """Returns (eve, asset_pv, liability_pv)."""
    asset_pv = sum(compute_position_pv(p, curve, max_months) for p in positions if p.side == "asset")
    liability_pv = sum(compute_position_pv(p, curve, max_months) for p in positions if p.side == "liability")
    return asset_pv - liability_pv, asset_pv, liability_pv


def compute_eve_sensitivity(positions, base_curve, shock_scenarios, total_equity=None, max_months=EVE_MAX_MONTHS):
    """
    shock_scenarios: {label: shift_fn(tenor_years) -> decimal}, same shape as
    curve/shocks.py builders. Returns a DataFrame: scenario, eve_full_reval,
    delta_eve_full_reval, delta_eve_pct_equity_full_reval.
    """
    total_assets = sum(p.balance for p in positions if p.side == "asset")
    total_liab = sum(p.balance for p in positions if p.side == "liability")
    equity = total_equity if total_equity else (total_assets - total_liab)
    equity = equity if equity and equity > 0 else config.EQUITY_CAPITAL_FALLBACK

    base_eve, _, _ = compute_eve(positions, base_curve, max_months)

    rows = []
    for label, shift_fn in shock_scenarios.items():
        shocked_curve = base_curve.shifted(shift_fn)
        shocked_eve, _, _ = compute_eve(positions, shocked_curve, max_months)
        delta_eve = shocked_eve - base_eve
        rows.append({
            "scenario": label, "eve_full_reval": shocked_eve,
            "delta_eve_full_reval": delta_eve, "delta_eve_pct_equity_full_reval": delta_eve / equity,
        })
    return pd.DataFrame(rows)
