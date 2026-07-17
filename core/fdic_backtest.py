"""
Real-actuals back-test: calibrate the balance sheet as of a past quarter,
replay the realized Treasury curve between then and now, and compare the
model's quarterly NII/NIM against what the bank actually reported over that
horizon. Replaces the circularity of core/backtest.py's demo path
(generate_sample_actuals is the model's own output with perturbed
assumptions, so it demonstrates the harness, not the model's accuracy).

The model's month 0 is a seed month: run_scenario computes it at the as-of
snapshot's own rates (that's the day-0 calibration invariant everywhere else
in this codebase), so it isn't a forecast of anything and is dropped before
aggregating to quarters. Months 1..3N are grouped into N quarters of three
months each (quarter 0 = months 1-3, ...), which lines up with the N actual
quarters data_sources.fdic_history.fetch_quarterly_financials returns
starting the quarter immediately after the as-of snapshot.

Read the attribution the same way as the CSV-driven back-test (see
core/backtest.py's docstring): rate variance is what the curve actually did
vs. the frozen forecast, volume variance is what balances actually did,
residual is their interaction. The model does not know the bank's real
future growth, mix shifts, or pricing decisions, so nonzero error here is
expected and is the point of running this, not something to tune away.
"""

import datetime

from core import backtest, engine
from curve.scenarios import CurvePath
from data_sources import fdic_bank, fdic_history, treasury_curve


def aggregate_monthly_to_quarterly(summary_df):
    """
    Groups a run_scenario summary_df's months into consecutive 3-month
    quarters (0,1,2 -> quarter 0; 3,4,5 -> quarter 1; ...), summing NII,
    averaging earning assets, and re-deriving NIM = annualized quarterly NII
    over average earning assets. Returns a DataFrame with a `month` column
    holding the quarter index (0-based, renumbered from whatever `month`
    values were passed in), so it merges directly with
    core.backtest.compute_backtest and data_sources.fdic_history's output.
    """
    df = summary_df.sort_values("month").reset_index(drop=True)
    df["quarter"] = df.index // 3
    grouped = df.groupby("quarter").agg(
        net_interest_income=("net_interest_income", "sum"),
        avg_earning_assets=("avg_earning_assets", "mean"),
    ).reset_index()
    grouped["nim"] = grouped["net_interest_income"] * 4 / grouped["avg_earning_assets"]
    return grouped.rename(columns={"quarter": "month"})[["month", "net_interest_income", "avg_earning_assets", "nim"]]


def run(positions_template, cert_id, quarters_ago):
    """
    Full pipeline: as-of calibration, realized-curve replay, quarterly
    aggregation, attribution. Returns (backtest_df, snapshot_fin).
    """
    snapshot_fin = fdic_history.fetch_snapshot(cert_id, quarters_ago)
    asof_positions, asof_equity, _ = fdic_bank.calibrate_positions_to_bank(
        positions_template, cert_id, fin=snapshot_fin
    )

    snapshot_date = datetime.datetime.strptime(str(snapshot_fin["REPDTE"]), "%Y%m%d").date()
    today = datetime.date.today()
    horizon_months = quarters_ago * 3 + 1  # +1 for the seed month, dropped before aggregation

    hist_curves_by_month = treasury_curve.get_historical_curves(snapshot_date, today)
    snapshot_month_key = f"{snapshot_date.year:04d}-{snapshot_date.month:02d}"
    sorted_months = sorted(hist_curves_by_month.keys())
    if snapshot_month_key not in sorted_months:
        raise ValueError(f"No historical Treasury curve found for the as-of month {snapshot_month_key}")
    start_idx = sorted_months.index(snapshot_month_key)
    needed_months = sorted_months[start_idx:start_idx + horizon_months]
    if len(needed_months) < horizon_months:
        raise ValueError(
            f"Only {len(needed_months)} months of historical Treasury curves available "
            f"from {snapshot_month_key} to {today}, need {horizon_months}"
        )
    curve_path = CurvePath([hist_curves_by_month[m] for m in needed_months], label="fdic-backtest-realized-curve")

    summary_df, _, _ = engine.run_scenario(
        asof_positions, curve_path, scenario_label="fdic-backtest", initial_equity=asof_equity
    )
    forecast_quarterly_df = aggregate_monthly_to_quarterly(summary_df[summary_df["month"] > 0])
    actuals_df = fdic_history.fetch_quarterly_financials(cert_id, quarters_ago)

    backtest_df = backtest.compute_backtest(forecast_quarterly_df, actuals_df)
    return backtest_df, snapshot_fin
