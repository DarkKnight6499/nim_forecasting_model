"""
Funds Transfer Pricing (FTP) and ALM/Treasury desk P&L.

Real banks don't just report a single blended NIM - business units are
charged/credited an internal transfer rate for the funds they use/provide,
so profitability splits into:

  Customer margin  - what the business unit actually earns/pays relative to
                     the internal transfer price (asset yield minus FTP charge;
                     FTP credit minus liability cost).
  ALM/Treasury P&L - what's left: the transfer-pricing net (FTP charged to
                     assets minus FTP credited to liabilities). This is the
                     book Treasury/ALM actually manages, and it's exactly
                     "ALM NII" - separate from customer-facing NIM.

These two always sum back to total NII by construction (see the identity
check in compute_ftp_pnl) - FTP is a zero-sum internal transfer, it doesn't
create or destroy income, only reallocates which desk "owns" it.

Methodology used here: matched-maturity FTP. Each bucket is assigned a
transfer-pricing tenor equal to its own effective duration/average life
(model/alm_reports.bucket_effective_duration - the same tenor already used
for EVE, so a bucket's "how long is this money really tied up for" answer is
consistent across reports). The FTP rate for that bucket in month t is the
benchmark rate at t plus a spread read off `config.FTP_CURVE_SPREADS_BY_TENOR_YEARS`,
with a management overlay flooring short-tenor spreads (mirrors a real
episode: a curve-implied FTP methodology pricing short tenors too low during
a rate-cutting cycle, corrected by an overlay).
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
    buckets: list of config.Bucket for this scenario run.
    bucket_detail_df: one scenario's detail_df from model.engine.run_scenario
                       (columns: month, bucket, side, balance, rate, interest).
    benchmark_path: this scenario's monthly benchmark rate array.
    benchmark_rate_for_tenors: benchmark level used to compute matched-maturity
        tenors (kept fixed across scenarios so a bucket's assigned FTP tenor
        doesn't itself shift with the rate scenario - only the curve level does).

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
