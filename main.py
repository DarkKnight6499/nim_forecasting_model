"""
Net Interest Margin (NIM) forecasting model - CLI entry point.

Examples:
  python main.py
      Run with the default synthetic balance sheet, no FRED key (uses config
      fallback benchmark rate).

  python main.py --fred-api-key YOUR_KEY
      Anchor the benchmark rate to the latest real Fed Funds rate from FRED.

  python main.py --bank-name "JPMORGAN CHASE BANK"
      Search FDIC for a bank by name (prints candidates + CERT numbers; run
      again with --bank-cert to actually calibrate to one).

  python main.py --bank-cert 3510 --fred-api-key YOUR_KEY --months 24
      Calibrate the balance sheet to a real bank (by FDIC certificate number)
      and run the full scenario set.
"""

import argparse
import os
from pathlib import Path

import pandas as pd

import config
from data_sources import fred_rates, fdic_bank
from model import scenarios, engine, alm_reports, ftp
from reporting import charts, export


def main():
    parser = argparse.ArgumentParser(description="NIM forecasting model")
    parser.add_argument("--bank-name", type=str, default=None, help="Search FDIC BankFind for a bank by name and exit")
    parser.add_argument("--bank-cert", type=int, default=None, help="FDIC certificate number to calibrate the balance sheet to")
    parser.add_argument("--fred-api-key", type=str, default=None, help="FRED API key (or set FRED_API_KEY env var)")
    parser.add_argument("--months", type=int, default=config.HORIZON_MONTHS, help="Forecast horizon in months")
    parser.add_argument("--output-dir", type=str, default="outputs", help="Directory for charts/Excel output")
    args = parser.parse_args()

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.bank_name:
        print(f"Searching FDIC for '{args.bank_name}'...")
        candidates = fdic_bank.find_bank(args.bank_name)
        if not candidates:
            print("No matches found.")
        for c in candidates:
            print(f"  CERT {c.get('CERT')}: {c.get('NAME')} - {c.get('CITY')}, {c.get('STALP')} (assets ${c.get('ASSET')}k)")
        print("\nRe-run with --bank-cert <CERT> to calibrate the model to one of these.")
        return

    buckets = config.DEFAULT_BUCKETS
    total_equity = None
    if args.bank_cert:
        try:
            buckets, total_equity = fdic_bank.calibrate_buckets_to_bank(buckets, args.bank_cert)
        except Exception as e:
            print(f"[main] FDIC calibration failed ({e}); falling back to synthetic balance sheet.")

    benchmark_rate, as_of = fred_rates.get_latest_benchmark_rate(
        api_key=args.fred_api_key, fallback=config.STARTING_BENCHMARK_RATE
    )
    if as_of:
        print(f"[main] Benchmark rate anchored to FRED DFF as of {as_of}: {benchmark_rate:.2%}")
    else:
        print(f"[main] Using fallback benchmark rate: {benchmark_rate:.2%}")

    scenario_paths = scenarios.build_all_scenarios(
        benchmark_rate, config.RATE_SCENARIOS, args.months, config.RAMP_MONTHS
    )

    combined_summary, details_by_scenario = engine.run_all_scenarios(buckets, scenario_paths)

    print("\n=== NIM by scenario (annualized) ===")
    pivot = combined_summary.pivot(index="month", columns="scenario", values="nim") * 100
    for m in [0, args.months // 2, args.months - 1]:
        row = pivot.loc[m]
        print(f"Month {m:>2}: " + "  ".join(f"{k}={v:.2f}%" for k, v in row.items()))

    base_label = "Base (flat)"
    base_summary = combined_summary[combined_summary["scenario"] == base_label].reset_index(drop=True)
    end_month = args.months - 1
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
    print("\n=== Rate sensitivity (NIM at end of horizon vs base) ===")
    print(sensitivity_df.to_string(index=False))

    # ---------------- ALM suite: gap, duration/EVE, liquidity, EaR ----------------
    gap_df = alm_reports.compute_rate_sensitivity_gap(buckets)
    print("\n=== Interest Rate Sensitivity Gap (repricing, $) ===")
    print(gap_df.round(0).to_string(index=False))

    total_assets_now = sum(b.balance for b in buckets if b.side == "asset")
    liquidity_df = alm_reports.compute_structural_liquidity(buckets, total_assets_now)
    print("\n=== Structural Liquidity Statement (cumulative gap % of assets) ===")
    print(liquidity_df[["band", "inflows", "outflows", "net_gap", "cumulative_gap_pct_assets", "breaches_tolerance"]]
          .round(4).to_string(index=False))

    duration_df, duration_summary, eve_df = alm_reports.compute_duration_gap(
        buckets, benchmark_rate, config.RATE_SCENARIOS, total_equity=total_equity
    )
    print(f"\n=== Duration Gap === "
          f"D(assets)={duration_summary['duration_assets_years']:.2f}y  "
          f"D(liabilities)={duration_summary['duration_liabilities_years']:.2f}y  "
          f"Duration Gap={duration_summary['duration_gap_years']:.2f}y "
          f"(equity basis: ${duration_summary['equity_used']:,.0f})")
    print("\n=== EVE Sensitivity ===")
    print(eve_df.assign(delta_eve_pct_equity=(eve_df["delta_eve_pct_equity"] * 100).round(2)).to_string(index=False))

    ear_horizons = tuple(h for h in (3, 6, 12, 24) if h <= args.months)
    ear_df = alm_reports.compute_earnings_at_risk(combined_summary, base_label, horizons=ear_horizons)
    print(f"\n=== Earnings-at-Risk (cumulative NII vs base, by horizon) ===")
    print(ear_df.assign(ear_pct_of_base=(ear_df["ear_pct_of_base"] * 100).round(3)).round(0).to_string(index=False))
    nii_delta_df = alm_reports.monthly_nii_delta(combined_summary, base_label)

    # ---------------- FTP / ALM desk P&L ----------------
    ftp_detail_df, ftp_monthly_df = ftp.compute_ftp_pnl(
        buckets, details_by_scenario[base_label], scenario_paths[base_label], benchmark_rate
    )
    max_err = ftp_monthly_df["identity_check"].abs().max()
    print(f"\n=== FTP / ALM Desk P&L (base scenario) === "
          f"(customer margin + ALM desk P&L reconciles to total NII, max abs error ${max_err:,.2f})")
    print(ftp_monthly_df[["month", "total_customer_margin", "alm_desk_pnl", "total_nii"]]
          .round(0).head(6).to_string(index=False))
    print("...")

    # ALM desk P&L stability across rate scenarios - this is what a real FTP policy
    # review checks (see README): a well-calibrated FTP curve keeps this roughly flat.
    print("\n=== ALM Desk P&L stability across rate scenarios (month 0 vs end of horizon) ===")
    for label, path in scenario_paths.items():
        _, monthly = ftp.compute_ftp_pnl(buckets, details_by_scenario[label], path, benchmark_rate)
        start_pnl = monthly.loc[monthly["month"] == 0, "alm_desk_pnl"].iloc[0]
        end_pnl = monthly.loc[monthly["month"] == monthly["month"].max(), "alm_desk_pnl"].iloc[0]
        print(f"  {label:>12}: month 0 = ${start_pnl:,.0f}/mo   ->   final month = ${end_pnl:,.0f}/mo")

    charts.plot_nim_by_scenario(combined_summary, out_dir / "nim_by_scenario.png")
    charts.plot_yield_cost_spread(base_summary, out_dir / "base_yield_cost_spread.png")
    charts.plot_balance_sheet_mix(details_by_scenario[base_label], out_dir / "balance_sheet_mix.png", month=0)
    charts.plot_rate_sensitivity_gap(gap_df, out_dir / "rate_sensitivity_gap.png")
    charts.plot_eve_sensitivity(eve_df, out_dir / "eve_sensitivity.png")
    charts.plot_liquidity_gap(liquidity_df, out_dir / "structural_liquidity.png",
                               tolerance_pct=config.LIQUIDITY_GAP_TOLERANCE_PCT_ASSETS)
    charts.plot_earnings_at_risk(nii_delta_df, out_dir / "earnings_at_risk.png")
    charts.plot_ftp_alm_pnl(ftp_monthly_df, out_dir / "ftp_alm_pnl.png")

    export.export_excel(
        out_dir / "nim_forecast.xlsx", combined_summary, details_by_scenario, sensitivity_df,
        gap_df=gap_df, duration_df=duration_df, duration_summary=duration_summary,
        eve_df=eve_df, liquidity_df=liquidity_df, ear_df=ear_df,
        ftp_monthly_df=ftp_monthly_df, ftp_detail_df=ftp_detail_df,
    )

    print(f"\nOutputs written to {out_dir.resolve()}")
    print("  - nim_forecast.xlsx (NIM, gap, duration/EVE, liquidity, earnings-at-risk, FTP/ALM P&L, bucket detail)")
    print("  - nim_by_scenario.png, base_yield_cost_spread.png, balance_sheet_mix.png,")
    print("    rate_sensitivity_gap.png, eve_sensitivity.png, structural_liquidity.png, ftp_alm_pnl.png")


if __name__ == "__main__":
    main()
