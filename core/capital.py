"""
Capital-lite: a standardized-approach RWA proxy and CET1 ratio, so rate
scenarios can be ranked on capital impact, not just NIM/EVE/liquidity.

RWA = sum(asset balance * Position.rwa_density); densities are standardized-
approach flavors, set per position in balance_sheet.yaml and documented
there (sovereign/cash 0%, agency MBS 20%, residential mortgage 50%,
corporate/C&I/CRE 100%, munis 50%, regulatory-retail consumer loans 75%).
Liabilities and equity carry no RWA (rwa_density defaults to 0.0 for them,
same field, simply irrelevant on that side of the balance sheet).

CET1 is the equity path core/engine.py already tracks (retained NII net of
config.DIVIDEND_PAYOUT_RATIO paid out as dividends each month) - this module
doesn't re-derive equity, it just divides the two: cet1_ratio = CET1 / RWA.
"""

import pandas as pd


def compute_rwa(positions, balances):
    """balances: {position.name: balance $} for the month being evaluated."""
    total = 0.0
    breakdown = {}
    for p in positions:
        if p.side != "asset":
            continue
        contribution = balances[p.name] * p.rwa_density
        breakdown[p.name] = contribution
        total += contribution
    return total, breakdown


def compute_cet1_ratio(positions, balances, cet1_capital):
    rwa, rwa_breakdown = compute_rwa(positions, balances)
    cet1_ratio = cet1_capital / rwa if rwa > 0 else float("nan")
    return {"rwa": rwa, "cet1_capital": cet1_capital, "cet1_ratio": cet1_ratio, "rwa_breakdown": rwa_breakdown}


def compute_cet1_by_month(positions, detail_df, summary_df):
    """
    detail_df: one scenario's position_detail_df (month, bucket, side, balance, ...).
    summary_df: that same scenario's summary_df (month, equity, ...).
    Returns a DataFrame: month, rwa, cet1_capital, cet1_ratio.
    """
    rows = []
    for month in sorted(detail_df["month"].unique()):
        balances = detail_df.loc[detail_df["month"] == month].set_index("bucket")["balance"].to_dict()
        cet1_capital = summary_df.loc[summary_df["month"] == month, "equity"].iloc[0]
        result = compute_cet1_ratio(positions, balances, cet1_capital)
        rows.append({"month": month, "rwa": result["rwa"], "cet1_capital": cet1_capital,
                      "cet1_ratio": result["cet1_ratio"]})
    return pd.DataFrame(rows)
