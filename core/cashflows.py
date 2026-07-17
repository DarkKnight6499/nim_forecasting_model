"""
Canonical cashflow generator: principal (from a Position's schedule) plus
coupon interest accrued on the outstanding balance each month. Used by both
full-revaluation EVE (core/eve.py) and AFS mark-to-market (core/mtm.py), so
there is one cashflow/PV convention instead of two independently-reinvented
ones (EVE's old principal-only ladder and MTM's inline coupon reconstruction
plus a day-0 book/PV ratio hack). FTP's own tranche PVs (core/ftp/) are a
genuinely different concept, transfer-pricing rates rather than cashflow
discounting, and are left alone.

Principal timing: EVE passes use_repricing_schedule=True, matching the
existing IRRBB convention (administered/NMD positions valued off their
behavioral-duration replicating ladder, not their liquidity-decay attrition
curve - the same convention already used by the gap report and
pooled_replicating FTP). MTM passes use_repricing_schedule=False (real
cashflow timing); this only ever applies to laddered AFS securities, where
the two schedules are identical, so the distinction is purely documentary
for MTM's case.

Variable (monthly-reset) positions are valued to their next reset: principal
plus one month's coupon accrual, the standard floater shortcut (a floater's
price does not move with the curve beyond its next reset date).

Each position's z-spread is solved once (solve_z_spread) against the curve
as of the valuation's base month, and held constant through the scenario:
PV(cashflows, discounted at zero_rate + z_spread) equals the position's book
balance at the base curve. This is the standard z-spread convention (a
constant spread over the discount curve), and it gives every valuation an
exact day-0 anchor to book value for an economic reason (the position's
credit/liquidity margin over Treasuries), not by dividing away whatever
approximation bias the cashflow reconstruction happens to have.
"""

import numpy as np

import config

Z_SPREAD_LOWER_BOUND = -0.05
Z_SPREAD_UPPER_BOUND = 0.20
Z_SPREAD_TOLERANCE = 0.01  # dollars of PV
Z_SPREAD_MAX_ITERATIONS = 60


def position_cashflows(position, max_months, current_balance=None, use_repricing_schedule=False):
    """
    Returns (principal, interest, leftover): principal/interest are arrays of
    length max_months for `current_balance` (default position.balance);
    leftover is the balance beyond max_months (assumed to arrive as a single
    lump cashflow at max_months, same convention as Position's own schedules).
    """
    balance = current_balance if current_balance is not None else position.balance

    if position.category_type == "variable":
        principal = np.zeros(max_months)
        interest = np.zeros(max_months)
        if max_months > 0:
            principal[0] = balance
            interest[0] = balance * position.rate / 12
        return principal, interest, 0.0

    schedule, leftover = (
        position.repricing_schedule(max_months) if use_repricing_schedule
        else position.cashflow_schedule(max_months)
    )
    scale = (balance / position.balance) if position.balance else 0.0
    principal = schedule * scale
    leftover *= scale

    # Interest accrues on the average balance outstanding during the month
    # (the declining balance is not a single point value once principal
    # amortizes intra-month). A coupon exactly equal to the discount rate
    # prices close to, but not exactly at, par: coupon accrual here is a
    # simple monthly rate (rate/12) while curve.spot's discount factor
    # compounds annually to a fractional-year power - two different
    # day-count conventions mixed, the same mismatch present everywhere
    # else in this codebase that discounts off YieldCurve.df, not something
    # unique to this module. The residual is a few basis points, not the
    # ~20% structural mispricing the old day-0 ratio calibration papered over.
    interest = np.zeros(max_months)
    remaining = balance
    for t in range(max_months):
        avg_balance = remaining - principal[t] / 2
        interest[t] = avg_balance * position.rate / 12
        remaining -= principal[t]

    return principal, interest, leftover


def pv(position, curve, z_spread, max_months=config.ALM_MAX_MONTHS, current_balance=None,
       use_repricing_schedule=False):
    """PV of position_cashflows, discounted at curve.spot(t) + z_spread at each tenor."""
    principal, interest, leftover = position_cashflows(position, max_months, current_balance, use_repricing_schedule)
    total = 0.0
    for t in range(max_months):
        cashflow = principal[t] + interest[t]
        if cashflow > 0:
            total += cashflow * _discount_factor(curve, (t + 1) / 12, z_spread)
    if leftover > 0:
        total += leftover * _discount_factor(curve, max_months / 12, z_spread)
    return total


def _discount_factor(curve, tenor_years, z_spread):
    if tenor_years <= 0:
        return 1.0
    return 1.0 / (1 + curve.spot(tenor_years) + z_spread) ** tenor_years


def solve_z_spread(position, curve, max_months=config.ALM_MAX_MONTHS, current_balance=None,
                    use_repricing_schedule=False):
    """
    Bisection: finds the constant spread s such that
    pv(position, curve, s, ...) equals current_balance (default
    position.balance). PV is monotonically decreasing in s.
    """
    balance = current_balance if current_balance is not None else position.balance
    if balance <= 0:
        return 0.0

    lo, hi = Z_SPREAD_LOWER_BOUND, Z_SPREAD_UPPER_BOUND
    mid = (lo + hi) / 2
    for _ in range(Z_SPREAD_MAX_ITERATIONS):
        mid = (lo + hi) / 2
        price = pv(position, curve, mid, max_months, current_balance, use_repricing_schedule)
        if abs(price - balance) < Z_SPREAD_TOLERANCE:
            break
        if price > balance:
            lo = mid
        else:
            hi = mid
    return mid
