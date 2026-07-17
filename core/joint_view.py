"""
Per-position joint view: NIM contribution, FTP customer margin, and LCR
impact (HQLA contribution for assets, weighted outflow contribution for
liabilities) side by side, plus a nim_per_unit_lcr_cost ratio answering
"which products earn the most margin per unit of liquidity cost/capacity".
"""

import numpy as np
import pandas as pd

from core.lcr import compute_hqla, compute_outflows


def compute_joint_view(positions, detail_df, summary_df, ftp_detail_df, month):
    month_detail = detail_df[detail_df["month"] == month].set_index("bucket")
    avg_earning_assets = summary_df.loc[summary_df["month"] == month, "avg_earning_assets"].iloc[0]
    balances = month_detail["balance"].to_dict()

    hqla = compute_hqla(positions, balances)
    _, outflow_breakdown = compute_outflows(positions, balances)
    ftp_month = ftp_detail_df[ftp_detail_df["month"] == month].set_index("bucket")

    rows = []
    for p in positions:
        interest = month_detail.loc[p.name, "interest"]
        signed_interest = interest if p.side == "asset" else -interest
        nim_contribution = signed_interest * 12 / avg_earning_assets if avg_earning_assets else np.nan
        customer_margin = ftp_month.loc[p.name, "customer_margin"] if p.name in ftp_month.index else np.nan

        if p.name in hqla["hqla_breakdown"]:
            lcr_impact, lcr_role = hqla["hqla_breakdown"][p.name], "hqla_contribution"
        elif p.name in outflow_breakdown:
            lcr_impact, lcr_role = outflow_breakdown[p.name], "outflow_contribution"
        else:
            lcr_impact, lcr_role = 0.0, None

        nim_per_unit_lcr_cost = signed_interest * 12 / lcr_impact if lcr_impact else np.nan

        rows.append({
            "position": p.name, "side": p.side, "balance": balances[p.name],
            "nim_contribution": nim_contribution, "ftp_customer_margin": customer_margin,
            "lcr_role": lcr_role, "lcr_impact": lcr_impact,
            "nim_per_unit_lcr_cost": nim_per_unit_lcr_cost,
        })

    return pd.DataFrame(rows)
