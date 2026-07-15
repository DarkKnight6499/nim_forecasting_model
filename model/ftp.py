"""
Funds Transfer Pricing (FTP) and ALM/Treasury desk P&L.

Splits total NII into customer margin (asset yield minus FTP charge; FTP
credit minus liability cost) and ALM/Treasury desk P&L (FTP charged to
assets minus FTP credited to liabilities). The two sum back to total NII by
construction (see identity_check in compute_ftp_pnl).

Each position is assigned a transfer-pricing tenor equal to its effective
duration (model/alm_reports.bucket_effective_duration). Its FTP rate is the
benchmark rate plus a spread read off config.FTP_CURVE_SPREADS_BY_TENOR_YEARS,
floored for short tenors at FTP_SHORT_TENOR_MIN_SPREAD.
"""

import numpy as np
import pandas as pd

import config
from model.alm_reports import bucket_effective_duration


def _ftp_spread_for_tenor(tenor_years):
    curve = config.FTP_CURVE_SPREADS_BY_TENOR_YEARS
    tenors = sorted(curve.keys())
    spreads = [curve[t] for t in tenors]
    spread = float(np.interp(tenor_years, tenors, spreads))
    if tenor_years < config.FTP_SHORT_TENOR_CUTOFF_YEARS:
        spread = max(spread, config.FTP_SHORT_TENOR_MIN_SPREAD)
    return spread


def compute_ftp_pnl(buckets, bucket_detail_df, benchmark_path, benchmark_rate_for_tenors):
    """
    buckets: list of core.position.Position for this scenario run.
    bucket_detail_df: one scenario's detail_df from core.engine.run_scenario
                       (columns: month, bucket, side, balance, rate, interest).
    benchmark_path: this scenario's monthly benchmark rate array.
    benchmark_rate_for_tenors: benchmark level used to compute matched-maturity
        tenors, kept fixed across scenarios.

    Returns (ftp_detail_df, ftp_monthly_df).
    """
    tenor_by_bucket = {b.name: bucket_effective_duration(b, benchmark_rate_for_tenors) for b in buckets}
    spread_by_bucket = {name: _ftp_spread_for_tenor(t) for name, t in tenor_by_bucket.items()}

    df = bucket_detail_df.sort_values(["bucket", "month"]).copy()
    df["prev_balance"] = df.groupby("bucket")["balance"].shift(1)
    df["avg_balance"] = np.where(df["prev_balance"].isna(), df["balance"],
                                  (df["balance"] + df["prev_balance"]) / 2)
    df["tenor_years"] = df["bucket"].map(tenor_by_bucket)
    df["ftp_spread"] = df["bucket"].map(spread_by_bucket)
    df["ftp_rate"] = df["month"].map(lambda t: benchmark_path[int(t)]) + df["ftp_spread"]
    df["ftp_amount"] = df["avg_balance"] * df["ftp_rate"] / 12
    df["customer_margin"] = np.where(
        df["side"] == "asset", df["interest"] - df["ftp_amount"], df["ftp_amount"] - df["interest"]
    )
    df = df.drop(columns=["prev_balance"])

    monthly = df.groupby("month").apply(lambda g: pd.Series({
        "total_customer_margin": g["customer_margin"].sum(),
        "ftp_charges_assets": g.loc[g["side"] == "asset", "ftp_amount"].sum(),
        "ftp_credits_liabilities": g.loc[g["side"] == "liability", "ftp_amount"].sum(),
        "total_nii": g["interest"].where(g["side"] == "asset", -g["interest"]).sum(),
    }), include_groups=False).reset_index()
    monthly["alm_desk_pnl"] = monthly["ftp_charges_assets"] - monthly["ftp_credits_liabilities"]
    monthly["identity_check"] = monthly["total_customer_margin"] + monthly["alm_desk_pnl"] - monthly["total_nii"]

    return df, monthly
