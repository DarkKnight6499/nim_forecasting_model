"""
AFS mark-to-market: revalues each AFS position's remaining principal
cashflow schedule off the scenario's curve each month, against its book
(amortized-cost) balance. HTM positions accrue only; this module is never
called for them, so they never contribute an MTM figure to NII.

The MTM buffer report caps unrealized AFS gains available for sale at
config.TRADING_LIMIT_PCT of the HTM book (a common regulatory-style limit on
trading-book capacity).
"""

import pandas as pd

import config


def _raw_pv(position, curve, current_balance, max_months=config.ALM_MAX_MONTHS):
    """
    PV of the position's remaining cashflow schedule (rescaled from its day-0
    balance to current_balance), reconstructing each month's coupon cashflow
    as accrued interest at the position's own coupon rate on the outstanding
    balance that month, discounted at curve's discount factor for that
    tenor. This is an approximation (a single blended coupon rate applied
    across a ladder whose cashflows span many tenors, against a curve that
    may not be flat), so it doesn't land exactly on book value even when the
    curve hasn't moved - see compute_afs_mtm_report's day-0 calibration.
    """
    schedule, leftover = position.cashflow_schedule(max_months)
    scale = current_balance / position.balance if position.balance > 0 else 0.0
    scaled_schedule = schedule * scale
    scaled_leftover = leftover * scale

    pv = 0.0
    remaining = current_balance
    for t, principal in enumerate(scaled_schedule):
        if remaining <= 0:
            break
        avg_balance = remaining - principal / 2
        interest = avg_balance * position.rate / 12
        pv += (principal + interest) * curve.df((t + 1) / 12)
        remaining -= principal
    if scaled_leftover > 0:
        pv += scaled_leftover * curve.df(max_months / 12)

    return pv


def compute_afs_mtm_report(positions, curve_path, detail_df):
    """
    positions: full position list for this scenario (AFS and HTM alike, so
    the HTM book total is available for the buffer cap).
    Returns (mtm_detail_df, mtm_summary_df):
      mtm_detail_df:  month, position, book_value, mtm_value, unrealized_gain (AFS only)
      mtm_summary_df: month, total_unrealized_gain, htm_book, buffer_limit, buffer_available

    Each position's raw PV formula (_raw_pv) is calibrated to equal book
    value exactly at month 0 (dividing by its own day-0 PV/book ratio), so
    reported gains reflect the curve's movement since day 0, not the
    approximation's own bias at an unchanged curve.
    """
    afs_positions = [p for p in positions if p.accounting == "AFS"]
    htm_names = [p.name for p in positions if p.accounting == "HTM"]
    months = sorted(detail_df["month"].unique())

    day0_detail = detail_df[detail_df["month"] == months[0]]
    calibration = {}
    for p in afs_positions:
        book0 = day0_detail.loc[day0_detail["bucket"] == p.name, "balance"].iloc[0]
        raw_pv0 = _raw_pv(p, curve_path.curves[months[0]], book0)
        calibration[p.name] = book0 / raw_pv0 if raw_pv0 else 1.0

    detail_rows = []
    summary_rows = []
    for month in months:
        curve = curve_path.curves[month]
        month_detail = detail_df[detail_df["month"] == month]
        htm_book = month_detail[month_detail["bucket"].isin(htm_names)]["balance"].sum()

        total_gain = 0.0
        for p in afs_positions:
            current_balance = month_detail.loc[month_detail["bucket"] == p.name, "balance"].iloc[0]
            mtm_value = _raw_pv(p, curve, current_balance) * calibration[p.name]
            gain = mtm_value - current_balance
            total_gain += gain
            detail_rows.append({"month": month, "position": p.name, "book_value": current_balance,
                                 "mtm_value": mtm_value, "unrealized_gain": gain})

        buffer_limit = config.TRADING_LIMIT_PCT * htm_book
        summary_rows.append({
            "month": month, "total_unrealized_gain": total_gain, "htm_book": htm_book,
            "buffer_limit": buffer_limit, "buffer_available": min(max(total_gain, 0.0), buffer_limit),
        })

    return pd.DataFrame(detail_rows), pd.DataFrame(summary_rows)
