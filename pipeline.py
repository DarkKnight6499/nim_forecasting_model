"""
Orchestration: runs the full scenario set and every downstream report,
returning one RunResults dataclass. No printing and no chart/Excel writing
happens here (see reporting/console.py and reporting/charts.py, export.py) -
this module's only job is building the numbers.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pandas as pd

import config
from curve import scenarios as curve_scenarios
from curve import shocks
from core import balance_sheet, engine
from core.ftp import aggregate as ftp
from core import lcr, mtm, joint_view, eve, nsfr, capital
from data_sources import fred_rates, fdic_bank, treasury_curve
from model import alm_reports

BASE_LABEL = "Base (flat)"


@dataclass
class RunResults:
    months: int
    output_dir: Path
    base_label: str
    end_month: int

    positions: list
    total_equity: Optional[float]
    lcr_calibration: dict
    bank_cert: Optional[int]
    bank_cert_calibration_error: Optional[str]

    deposit_history_path: Optional[str]
    deposit_history_log_rows: Optional[list]

    benchmark_rate: float
    benchmark_rate_as_of: Optional[str]
    base_curve: object
    curve_source: str
    curve_paths: dict

    combined_summary: pd.DataFrame
    details_by_scenario: dict
    cohort_details_by_scenario: dict
    base_summary: pd.DataFrame

    sensitivity_df: pd.DataFrame

    gap_df: pd.DataFrame
    liquidity_df: pd.DataFrame
    duration_df: pd.DataFrame
    duration_summary: dict
    eve_df: pd.DataFrame
    full_reval_eve_df: pd.DataFrame
    ear_df: pd.DataFrame
    nii_delta_df: pd.DataFrame

    ftp_detail_df: pd.DataFrame
    ftp_monthly_df: pd.DataFrame
    ftp_monthly_by_scenario: dict

    lcr_df: pd.DataFrame
    base_lcr: pd.DataFrame
    nsfr_df: pd.DataFrame
    base_nsfr: pd.DataFrame
    capital_df: pd.DataFrame

    joint_view_df: pd.DataFrame
    mtm_detail_df: pd.DataFrame
    mtm_summary_df: pd.DataFrame

    backtest_path: Optional[str]
    backtest_df: Optional[pd.DataFrame]
    backtest_fdic_quarters: Optional[int]
    fdic_backtest_df: Optional[pd.DataFrame]
    fdic_snapshot_fin: Optional[dict]
    fdic_backtest_message: Optional[str]

    ftp_recalibrate: bool
    ftp_before_variance: Optional[float]
    ftp_after_variance: Optional[float]
    ftp_calibrated_spreads: Optional[dict]


def search_bank(name_query):
    """Thin wrapper so main.py never imports data_sources directly."""
    return fdic_bank.find_bank(name_query)


def run(bank_cert=None, fred_api_key=None, months=config.HORIZON_MONTHS, output_dir="outputs",
        ftp_recalibrate=False, deposit_history=None, backtest=None, backtest_fdic=None) -> RunResults:
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    positions = balance_sheet.load_positions()
    total_equity = None
    lcr_calibration = {"additional_outflow": 0.0, "outflow_adjustment_pct": 1.0}
    bank_cert_calibration_error = None
    if bank_cert:
        try:
            positions, total_equity, lcr_calibration = fdic_bank.calibrate_positions_to_bank(positions, bank_cert)
        except Exception as e:
            bank_cert_calibration_error = str(e)

    deposit_history_log_rows = None
    if deposit_history:
        from core import nmd_estimation
        deposit_history_log_rows = nmd_estimation.apply_estimates_from_csv(positions, deposit_history)

    benchmark_rate, as_of = fred_rates.get_latest_benchmark_rate(
        api_key=fred_api_key, fallback=config.STARTING_BENCHMARK_RATE
    )

    base_curve, curve_source = treasury_curve.get_base_curve(
        benchmark_rate, config.FALLBACK_CURVE_TENORS, config.FALLBACK_CURVE_RATES
    )

    curve_paths = curve_scenarios.build_curve_scenarios(
        base_curve, config.RATE_SCENARIOS, months, config.RAMP_MONTHS
    )

    combined_summary, details_by_scenario, cohort_details_by_scenario = engine.run_all_scenarios(
        positions, curve_paths, initial_equity=total_equity
    )

    base_label = BASE_LABEL
    base_summary = combined_summary[combined_summary["scenario"] == base_label].reset_index(drop=True)
    end_month = months - 1
    base_end_nim = base_summary.loc[base_summary["month"] == end_month, "nim"].iloc[0]

    sensitivity_rows = []
    for label in config.RATE_SCENARIOS:
        scen_end_nim = combined_summary.loc[
            (combined_summary["scenario"] == label) & (combined_summary["month"] == end_month), "nim"
        ].iloc[0]
        sensitivity_rows.append({
            "scenario": label,
            f"nim_month_{end_month}": round(scen_end_nim * 100, 3),
            "delta_vs_base_bps": round((scen_end_nim - base_end_nim) * 10000, 1),
        })
    sensitivity_df = pd.DataFrame(sensitivity_rows)

    # ---------------- ALM suite: gap, duration/EVE, liquidity, EaR ----------------
    gap_df = alm_reports.compute_rate_sensitivity_gap(positions)

    total_assets_now = sum(p.balance for p in positions if p.side == "asset")
    liquidity_df = alm_reports.compute_structural_liquidity(positions, total_assets_now)

    shock_scenarios_scalar = {
        label: curve_scenarios.as_scenario_def(value).shift_fn(1.0)
        for label, value in config.RATE_SCENARIOS.items()
    }
    duration_df, duration_summary, eve_df = alm_reports.compute_duration_gap(
        positions, benchmark_rate, shock_scenarios_scalar, total_equity=total_equity
    )

    irrbb_scenarios = {
        "Parallel +200bps": shocks.parallel(200),
        "Parallel -200bps": shocks.parallel(-200),
        "Steepener": shocks.steepener(-50, 100),
        "Flattener": shocks.flattener(100, -50),
        "Short rate up": shocks.short_up(100),
        "Short rate down": shocks.short_down(100),
    }
    full_reval_eve_df = eve.compute_eve_sensitivity(positions, base_curve, irrbb_scenarios, total_equity=total_equity)

    ear_horizons = tuple(h for h in (3, 6, 12, 24) if h <= months)
    ear_df = alm_reports.compute_earnings_at_risk(combined_summary, base_label, horizons=ear_horizons)
    nii_delta_df = alm_reports.monthly_nii_delta(combined_summary, base_label)

    # ---------------- FTP / ALM desk P&L (every scenario, reused for the base-scenario report and the stability view) ----------------
    ftp_monthly_by_scenario = {}
    ftp_detail_df = None
    for label, path in curve_paths.items():
        detail_df, monthly_df = ftp.compute_ftp_pnl(
            positions, details_by_scenario[label], path, benchmark_rate,
            cohort_detail_df=cohort_details_by_scenario[label],
        )
        ftp_monthly_by_scenario[label] = monthly_df
        if label == base_label:
            ftp_detail_df = detail_df
    ftp_monthly_df = ftp_monthly_by_scenario[base_label]

    # ---------------- Liquidity Coverage Ratio (LCR), base and stressed ----------------
    lcr_rows = []
    for label, detail_df in details_by_scenario.items():
        for month in sorted(detail_df["month"].unique()):
            balances = detail_df.loc[detail_df["month"] == month].set_index("bucket")["balance"].to_dict()
            result = lcr.compute_lcr(positions, balances, **lcr_calibration)
            stressed_result = lcr.compute_lcr(positions, balances, **lcr_calibration, stressed=True)
            lcr_rows.append({"scenario": label, "month": month, "lcr": result["lcr"],
                              "lcr_stressed": stressed_result["lcr"],
                              "hqla": result["hqla"], "net_outflows": result["net_outflows"]})
    lcr_df = pd.DataFrame(lcr_rows)
    base_lcr = lcr_df[lcr_df["scenario"] == base_label]

    # ---------------- Net Stable Funding Ratio (NSFR) ----------------
    nsfr_rows = []
    for label, detail_df in details_by_scenario.items():
        summary = combined_summary[combined_summary["scenario"] == label]
        for month in sorted(detail_df["month"].unique()):
            balances = detail_df.loc[detail_df["month"] == month].set_index("bucket")["balance"].to_dict()
            equity_t = summary.loc[summary["month"] == month, "equity"].iloc[0]
            result = nsfr.compute_nsfr(positions, balances, equity_t)
            nsfr_rows.append({"scenario": label, "month": month, "nsfr": result["nsfr"],
                               "asf": result["asf"], "rsf": result["rsf"]})
    nsfr_df = pd.DataFrame(nsfr_rows)
    base_nsfr = nsfr_df[nsfr_df["scenario"] == base_label]

    # ---------------- Capital-lite: CET1 ratio (base scenario) ----------------
    capital_df = capital.compute_cet1_by_month(positions, details_by_scenario[base_label], base_summary)
    capital_df.insert(0, "scenario", base_label)

    # ---------------- Joint LCR-NIM view (base scenario, month 0) ----------------
    joint_view_df = joint_view.compute_joint_view(
        positions, details_by_scenario[base_label], base_summary, ftp_detail_df, month=0
    )

    # ---------------- AFS mark-to-market / MTM buffer ----------------
    mtm_detail_df, mtm_summary_df = mtm.compute_afs_mtm_report(
        positions, curve_paths[base_label], details_by_scenario[base_label]
    )

    # ---------------- Back-test vs actuals ----------------
    backtest_df = None
    if backtest:
        from core import backtest as backtest_module
        actuals_df = pd.read_csv(backtest)
        backtest_df = backtest_module.compute_backtest(base_summary, actuals_df)

    fdic_backtest_df = None
    fdic_snapshot_fin = None
    fdic_backtest_message = None
    if backtest_fdic:
        if not bank_cert:
            fdic_backtest_message = "--backtest-fdic requires --bank-cert (need a real bank to replay against)"
        else:
            from core import fdic_backtest as fdic_backtest_module
            try:
                fdic_backtest_df, fdic_snapshot_fin = fdic_backtest_module.run(
                    balance_sheet.load_positions(), bank_cert, backtest_fdic
                )
            except Exception as e:
                fdic_backtest_message = f"FDIC real-actuals back-test failed ({e})"

    ftp_before_variance = ftp_after_variance = ftp_calibrated_spreads = None
    if ftp_recalibrate:
        from core import ftp_calibration
        from curve.historical_cycles import HISTORICAL_CYCLES
        cycle_paths = {name: builder(base_curve, months) for name, builder in HISTORICAL_CYCLES.items()}
        ftp_before_variance = ftp_calibration.cross_cycle_variance(positions, cycle_paths, benchmark_rate)
        ftp_calibrated_spreads = ftp_calibration.calibrate_policy_spreads(positions, cycle_paths, benchmark_rate)
        ftp_after_variance = ftp_calibration.cross_cycle_variance(
            positions, cycle_paths, benchmark_rate, spreads_by_tenor=ftp_calibrated_spreads
        )

    return RunResults(
        months=months, output_dir=out_dir, base_label=base_label, end_month=end_month,
        positions=positions, total_equity=total_equity, lcr_calibration=lcr_calibration,
        bank_cert=bank_cert, bank_cert_calibration_error=bank_cert_calibration_error,
        deposit_history_path=deposit_history, deposit_history_log_rows=deposit_history_log_rows,
        benchmark_rate=benchmark_rate, benchmark_rate_as_of=as_of,
        base_curve=base_curve, curve_source=curve_source, curve_paths=curve_paths,
        combined_summary=combined_summary, details_by_scenario=details_by_scenario,
        cohort_details_by_scenario=cohort_details_by_scenario, base_summary=base_summary,
        sensitivity_df=sensitivity_df,
        gap_df=gap_df, liquidity_df=liquidity_df, duration_df=duration_df,
        duration_summary=duration_summary, eve_df=eve_df, full_reval_eve_df=full_reval_eve_df,
        ear_df=ear_df, nii_delta_df=nii_delta_df,
        ftp_detail_df=ftp_detail_df, ftp_monthly_df=ftp_monthly_df,
        ftp_monthly_by_scenario=ftp_monthly_by_scenario,
        lcr_df=lcr_df, base_lcr=base_lcr, nsfr_df=nsfr_df, base_nsfr=base_nsfr, capital_df=capital_df,
        joint_view_df=joint_view_df, mtm_detail_df=mtm_detail_df, mtm_summary_df=mtm_summary_df,
        backtest_path=backtest, backtest_df=backtest_df,
        backtest_fdic_quarters=backtest_fdic, fdic_backtest_df=fdic_backtest_df,
        fdic_snapshot_fin=fdic_snapshot_fin, fdic_backtest_message=fdic_backtest_message,
        ftp_recalibrate=ftp_recalibrate, ftp_before_variance=ftp_before_variance,
        ftp_after_variance=ftp_after_variance, ftp_calibrated_spreads=ftp_calibrated_spreads,
    )
