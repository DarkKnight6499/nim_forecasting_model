"""
Back-testing harness: compares the model's forecast against observed
actuals, with an exact rate/volume/residual attribution of the monthly NII
error.

Input CSV: month, actual_nii, actual_avg_earning_assets, actual_nim.

Attribution (monthly rate = nim/12, so units are dollars, matching
net_interest_income): with r0/v0 the forecast rate/volume and Dr/Dv the
actual-minus-forecast differences,

    actual_nii - forecast_nii = v0*Dr + r0*Dv + Dr*Dv
                              = rate_variance + volume_variance + residual

is an algebraic identity (expand (v0+Dv)*(r0+Dr) - v0*r0), so the three
components always sum exactly to the total error by construction - nii_error
here is defined as that sum, not independently recomputed from the actuals
CSV's own actual_nii column, so the reconciliation holds even if a real
actuals file's three columns aren't perfectly self-consistent. residual is
the cross (rate-times-volume) interaction term: not attributable to either
factor alone, hence "unmodellable" - a mix/interaction effect, not an error
in the model.
"""

import copy

import pandas as pd

from core import engine
from curve import shocks
from curve.scenarios import CurvePath


def compute_backtest(forecast_summary_df, actuals_df):
    """
    forecast_summary_df: a single scenario's summary_df from core.engine.run_scenario
                          (or run_all_scenarios's combined_summary_df pre-filtered to one scenario).
    actuals_df: columns month, actual_nii, actual_avg_earning_assets, actual_nim.
    """
    merged = forecast_summary_df.merge(actuals_df, on="month", how="inner")

    forecast_rate = merged["nim"] / 12
    actual_rate = merged["actual_nim"] / 12
    forecast_vol = merged["avg_earning_assets"]
    actual_vol = merged["actual_avg_earning_assets"]

    merged["rate_variance"] = forecast_vol * (actual_rate - forecast_rate)
    merged["volume_variance"] = forecast_rate * (actual_vol - forecast_vol)
    merged["residual_unmodellable"] = (actual_rate - forecast_rate) * (actual_vol - forecast_vol)
    merged["nii_error"] = merged["rate_variance"] + merged["volume_variance"] + merged["residual_unmodellable"]
    merged["cumulative_nii_error"] = merged["nii_error"].cumsum()
    merged["nim_error_bps"] = (merged["actual_nim"] - merged["nim"]) * 10000

    return merged[[
        "month", "net_interest_income", "actual_nii", "nii_error", "cumulative_nii_error",
        "nim", "actual_nim", "nim_error_bps", "rate_variance", "volume_variance", "residual_unmodellable",
    ]]


def generate_sample_actuals(positions, curve_path, growth_perturbation=1.15, rate_perturbation_bps=25):
    """
    Runs the model itself with perturbed growth rates and a small parallel
    rate offset, then reformats its own output into the actuals CSV schema -
    so the back-test harness is demonstrable without needing a real bank's
    reported financials.
    """
    perturbed = copy.deepcopy(positions)
    for p in perturbed:
        p.growth_rate_annual *= growth_perturbation
    perturbed_curve_path = CurvePath(
        [c.shifted(shocks.parallel(rate_perturbation_bps)) for c in curve_path.curves], label="actuals"
    )

    summary_df, _, _ = engine.run_scenario(perturbed, perturbed_curve_path, scenario_label="actuals")
    return pd.DataFrame({
        "month": summary_df["month"],
        "actual_nii": summary_df["net_interest_income"],
        "actual_avg_earning_assets": summary_df["avg_earning_assets"],
        "actual_nim": summary_df["nim"],
    })
