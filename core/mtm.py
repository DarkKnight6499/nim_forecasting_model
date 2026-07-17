"""
AFS mark-to-market: revalues each AFS position's remaining cashflow schedule
(principal plus coupon interest, via core/cashflows.py) off the scenario's
curve each month, against its book (amortized-cost) balance. HTM positions
accrue only; this module is never called for them, so they never contribute
an MTM figure to NII.

Each AFS position's z-spread is solved once against the base (month-0)
curve, so month-0 unrealized gain is exactly $0 by construction (the spread
makes PV equal book value at that curve); every later month's gain reflects
purely the curve's movement since day 0, not any residual cashflow-
reconstruction bias. This replaces the previous day-0 book/PV ratio
calibration, which divided away a structural mispricing rather than solving
for an economically meaningful spread (the position's own credit/liquidity
margin over Treasuries).

The MTM buffer report caps unrealized AFS gains available for sale at
config.TRADING_LIMIT_PCT of the HTM book (a common regulatory-style limit on
trading-book capacity).
"""

import pandas as pd

import config
from core import cashflows


def compute_afs_mtm_report(positions, curve_path, detail_df):
    """
    positions: full position list for this scenario (AFS and HTM alike, so
    the HTM book total is available for the buffer cap).
    Returns (mtm_detail_df, mtm_summary_df):
      mtm_detail_df:  month, position, book_value, mtm_value, unrealized_gain (AFS only)
      mtm_summary_df: month, total_unrealized_gain, htm_book, buffer_limit, buffer_available
    """
    afs_positions = [p for p in positions if p.accounting == "AFS"]
    htm_names = [p.name for p in positions if p.accounting == "HTM"]
    months = sorted(detail_df["month"].unique())

    day0_detail = detail_df[detail_df["month"] == months[0]]
    day0_curve = curve_path.curves[months[0]]
    z_spreads = {}
    for p in afs_positions:
        book0 = day0_detail.loc[day0_detail["bucket"] == p.name, "balance"].iloc[0]
        z_spreads[p.name] = cashflows.solve_z_spread(p, day0_curve, current_balance=book0)

    detail_rows = []
    summary_rows = []
    for month in months:
        curve = curve_path.curves[month]
        month_detail = detail_df[detail_df["month"] == month]
        htm_book = month_detail[month_detail["bucket"].isin(htm_names)]["balance"].sum()

        total_gain = 0.0
        for p in afs_positions:
            current_balance = month_detail.loc[month_detail["bucket"] == p.name, "balance"].iloc[0]
            mtm_value = cashflows.pv(p, curve, z_spreads[p.name], current_balance=current_balance)
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
