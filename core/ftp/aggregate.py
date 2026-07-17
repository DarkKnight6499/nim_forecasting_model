"""
Funds Transfer Pricing (FTP) and ALM/Treasury desk P&L.

Splits total NII into customer margin (asset yield minus FTP charge; FTP
credit minus liability cost) and ALM/Treasury desk P&L (FTP charged to
assets minus FTP credited to liabilities). The two sum back to total NII by
construction (see identity_check).

Each position's FTP rate series comes from its own ftp_method
(core/ftp/registry.py), so different products can be transfer-priced by
different methods within the same run.
"""

import numpy as np
import pandas as pd

from core.ftp.registry import build_rate_series


def compute_ftp_pnl(positions, position_detail_df, curve_path, benchmark_rate_for_tenors,
                     cohort_detail_df=None, spreads_by_tenor=None):
    """
    positions: list of core.position.Position for this scenario run.
    position_detail_df: one scenario's detail_df from core.engine.run_scenario
                         (columns: month, bucket, side, balance, rate, interest).
    curve_path: this scenario's curve.scenarios.CurvePath.
    benchmark_rate_for_tenors: benchmark level used to size matched-maturity/
        pooled-replicating tenors, kept fixed across scenarios.
    cohort_detail_df: this scenario's cohort_detail_df from core.engine.run_scenario,
        needed by matched_maturity's origination-locked fixed_amortizing pricing.
    spreads_by_tenor: overrides config.FTP_CURVE_SPREADS_BY_TENOR_YEARS (core/ftp_calibration.py uses this).

    Returns (ftp_detail_df, ftp_monthly_df).
    """
    rate_series_by_position = {
        p.name: build_rate_series(p, curve_path, benchmark_rate_for_tenors,
                                   cohort_detail_df=cohort_detail_df, spreads_by_tenor=spreads_by_tenor)
        for p in positions
    }

    df = position_detail_df.sort_values(["bucket", "month"]).copy()
    df["prev_balance"] = df.groupby("bucket")["balance"].shift(1)
    df["avg_balance"] = np.where(df["prev_balance"].isna(), df["balance"],
                                  (df["balance"] + df["prev_balance"]) / 2)
    df["ftp_rate"] = df.apply(lambda row: rate_series_by_position[row["bucket"]][int(row["month"])], axis=1)
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
