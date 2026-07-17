"""
Point-in-time (no growth) ALM reports on the current balance sheet:

  - Interest Rate Sensitivity Gap: RSA vs RSL by repricing time band, and
    the resulting gap / cumulative gap.
  - Duration Gap & EVE sensitivity: delta economic value of equity under
    each instantaneous rate shock, using effective duration per position.
  - Structural Liquidity Statement: same time bands, cashflow (not
    repricing) turnover.
  - Earnings-at-Risk (EaR): cumulative NII impact vs. base by horizon, read
    from the dynamic engine's (core/engine.py) own monthly output.

Gap and liquidity schedules are computed by core.position.Position
(repricing_schedule / cashflow_schedule).
"""

import numpy as np
import pandas as pd

import config
from core.position import bucket_effective_duration


def _band_table(positions, schedule_method, max_months=config.ALM_MAX_MONTHS, bands=None, asset_col="RSA", liab_col="RSL"):
    bands = bands or config.ALM_TIME_BANDS
    band_names = [b[0] for b in bands] + [">5Y"]
    totals = {name: {asset_col: 0.0, liab_col: 0.0} for name in band_names}

    for p in positions:
        schedule, leftover = getattr(p, schedule_method)(max_months)
        col = asset_col if p.side == "asset" else liab_col
        for name, lo, hi in bands:
            totals[name][col] += schedule[lo:hi].sum()
        totals[">5Y"][col] += leftover

    rows = []
    cum = 0.0
    for name in band_names:
        a = totals[name][asset_col]
        l = totals[name][liab_col]
        gap = a - l
        cum += gap
        rows.append({"band": name, asset_col: a, liab_col: l, "gap": gap, "cumulative_gap": cum,
                      "gap_ratio": (a / l) if l else np.nan})
    return pd.DataFrame(rows)


def compute_rate_sensitivity_gap(positions):
    """RSA/RSL by repricing time band, gap, cumulative gap, gap ratio."""
    return _band_table(positions, "repricing_schedule", asset_col="RSA", liab_col="RSL")


def compute_structural_liquidity(positions, total_assets):
    """
    Time-banded funding/liquidity gap: inflows (assets maturing/paying down =
    cash available) vs outflows (liabilities maturing or, for core deposits,
    attriting = funding that must be replaced). Uses Position.cashflow_schedule,
    which treats non-maturity deposits by their core-deposit decay assumption
    rather than their (much faster) rate-repricing timing.
    """
    df = _band_table(positions, "cashflow_schedule", asset_col="inflows", liab_col="outflows")
    df = df.rename(columns={"gap": "net_gap", "cumulative_gap": "cumulative_net_gap", "gap_ratio": "inflow_outflow_ratio"})
    df["cumulative_gap_pct_assets"] = df["cumulative_net_gap"] / total_assets
    df["breaches_tolerance"] = df["cumulative_gap_pct_assets"] < config.LIQUIDITY_GAP_TOLERANCE_PCT_ASSETS
    return df


def compute_duration_gap(buckets, benchmark_rate, shock_scenarios, total_equity=None):
    """
    Returns (bucket_duration_df, summary_dict, eve_sensitivity_df).

    summary_dict has weighted asset/liability duration and the duration gap:
        DGAP = D_assets - (Total Liabilities / Total Assets) * D_liabilities

    eve_sensitivity_df: for each scenario's instantaneous shock,
    Delta-EVE = -D_assets * Assets * shock - (-D_liab * Liabilities * shock),
    reported in $ and as % of total equity.
    """
    rows = []
    total_assets = total_liab = dv_assets = dv_liab = 0.0
    for b in buckets:
        dur = bucket_effective_duration(b, benchmark_rate)
        rows.append({"bucket": b.name, "side": b.side, "balance": b.balance, "duration_years": round(dur, 3)})
        if b.side == "asset":
            total_assets += b.balance
            dv_assets += b.balance * dur
        else:
            total_liab += b.balance
            dv_liab += b.balance * dur

    da = dv_assets / total_assets if total_assets else np.nan
    dl = dv_liab / total_liab if total_liab else np.nan
    duration_gap = da - (total_liab / total_assets) * dl if total_assets else np.nan

    equity = total_equity if total_equity else (total_assets - total_liab)
    equity = equity if equity and equity > 0 else config.EQUITY_CAPITAL_FALLBACK

    eve_rows = []
    for label, shock in shock_scenarios.items():
        d_mv_assets = -dv_assets * shock
        d_mv_liab = -dv_liab * shock
        d_eve = d_mv_assets - d_mv_liab
        eve_rows.append({
            "scenario": label, "shock_bps": round(shock * 10000, 0),
            "delta_eve": d_eve, "delta_eve_pct_equity": d_eve / equity,
        })

    summary = {
        "total_assets": total_assets, "total_liabilities": total_liab,
        "duration_assets_years": da, "duration_liabilities_years": dl,
        "duration_gap_years": duration_gap, "equity_used": equity,
    }
    return pd.DataFrame(rows), summary, pd.DataFrame(eve_rows)


def compute_earnings_at_risk(combined_summary_df, base_label, horizons=(3, 6, 12, 24)):
    """Cumulative NII impact vs. base under each scenario, at several horizons."""
    max_month = int(combined_summary_df["month"].max())
    rows = []
    for horizon in horizons:
        if horizon - 1 > max_month:
            continue
        base_nii = combined_summary_df[
            (combined_summary_df["scenario"] == base_label) & (combined_summary_df["month"] < horizon)
        ]["net_interest_income"].sum()
        for label in combined_summary_df["scenario"].unique():
            nii = combined_summary_df[
                (combined_summary_df["scenario"] == label) & (combined_summary_df["month"] < horizon)
            ]["net_interest_income"].sum()
            rows.append({
                "horizon_months": horizon, "scenario": label, "nii": nii,
                "ear_dollar": nii - base_nii,
                "ear_pct_of_base": (nii - base_nii) / base_nii if base_nii else np.nan,
            })
    return pd.DataFrame(rows)


def monthly_nii_delta(combined_summary_df, base_label):
    """Month-by-month NII delta vs. base, per scenario - the series that reveals EaR crossovers."""
    base = combined_summary_df[combined_summary_df["scenario"] == base_label].set_index("month")["net_interest_income"]
    rows = []
    for label, grp in combined_summary_df.groupby("scenario"):
        s = grp.set_index("month")["net_interest_income"]
        delta = s - base
        for month, val in delta.items():
            rows.append({"month": month, "scenario": label, "nii_delta": val})
    return pd.DataFrame(rows)
