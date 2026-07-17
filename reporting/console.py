"""
Every console report the CLI prints, grouped into one function per report,
consuming a pipeline.RunResults. This is the only module in the codebase
(besides data_sources/* diagnostic logging, a separate cross-cutting
concern) allowed to call print() for the model's structured report output.
"""

import config


def print_bank_search(name_query, candidates):
    print(f"Searching FDIC for '{name_query}'...")
    if not candidates:
        print("No matches found.")
    for c in candidates:
        print(f"  CERT {c.get('CERT')}: {c.get('NAME')} - {c.get('CITY')}, {c.get('STALP')} (assets ${c.get('ASSET')}k)")
    print("\nRe-run with --bank-cert <CERT> to calibrate the model to one of these.")


def print_bank_cert_calibration_error(error):
    print(f"[main] FDIC calibration failed ({error}); falling back to synthetic balance sheet.")


def print_deposit_history_estimates(path, log_rows):
    if log_rows:
        print(f"\n=== NMD behavioral estimates from {path} (assumed vs estimated) ===")
        for row in log_rows:
            assumed = f"{row['assumed']:.4f}" if row["assumed"] is not None else "None"
            print(f"  {row['position']:<28} {row['param']:<26} assumed={assumed:>10}  estimated={row['estimated']:.4f}")
    else:
        print(f"\n[main] No products in {path} matched a Core/Non-Core position pair.")


def print_curve_info(r):
    if r.benchmark_rate_as_of:
        print(f"[main] Benchmark rate anchored to FRED DFF as of {r.benchmark_rate_as_of}: {r.benchmark_rate:.2%}")
    else:
        print(f"[main] Using fallback benchmark rate: {r.benchmark_rate:.2%}")
    print(f"[main] Base yield curve: {r.curve_source} "
          f"(1M={r.base_curve.spot(1/12):.2%}, 2Y={r.base_curve.spot(2):.2%}, 10Y={r.base_curve.spot(10):.2%})")


def print_nim_by_scenario(r):
    print("\n=== NIM by scenario (annualized) ===")
    pivot = r.combined_summary.pivot(index="month", columns="scenario", values="nim") * 100
    for m in [0, r.months // 2, r.months - 1]:
        row = pivot.loc[m]
        print(f"Month {m:>2}: " + "  ".join(f"{k}={v:.2f}%" for k, v in row.items()))


def print_sensitivity(r):
    print("\n=== Rate sensitivity (NIM at end of horizon vs base) ===")
    print(r.sensitivity_df.to_string(index=False))


def print_gap(r):
    print("\n=== Interest Rate Sensitivity Gap (repricing, $) ===")
    print(r.gap_df.round(0).to_string(index=False))


def print_liquidity(r):
    print("\n=== Structural Liquidity Statement (cumulative gap % of assets) ===")
    print(r.liquidity_df[["band", "inflows", "outflows", "net_gap", "cumulative_gap_pct_assets", "breaches_tolerance"]]
          .round(4).to_string(index=False))


def print_duration_and_eve(r):
    print(f"\n=== Duration Gap === "
          f"D(assets)={r.duration_summary['duration_assets_years']:.2f}y  "
          f"D(liabilities)={r.duration_summary['duration_liabilities_years']:.2f}y  "
          f"Duration Gap={r.duration_summary['duration_gap_years']:.2f}y "
          f"(equity basis: ${r.duration_summary['equity_used']:,.0f})")
    print("\n=== EVE Sensitivity (linear duration approximation) ===")
    print("(basis-only scenarios show 0.00 here: this approximation only sees curve shocks, not index basis shocks)")
    print(r.eve_df.assign(delta_eve_pct_equity=(r.eve_df["delta_eve_pct_equity"] * 100).round(2)).to_string(index=False))


def print_full_reval_eve(r):
    print("\n=== Full-Revaluation EVE Sensitivity (standard six IRRBB scenarios) ===")
    print(r.full_reval_eve_df.assign(
        delta_eve_pct_equity_full_reval=(r.full_reval_eve_df["delta_eve_pct_equity_full_reval"] * 100).round(2)
    ).round(0).to_string(index=False))
    print("  (compare Parallel +/-200bps here against the linear duration approximation above for convexity:")
    print("   full revaluation captures the price-yield curve's curvature, the linear approximation doesn't.)")


def print_ear(r):
    print("\n=== Earnings-at-Risk (cumulative NII vs base, by horizon) ===")
    print(r.ear_df.assign(ear_pct_of_base=(r.ear_df["ear_pct_of_base"] * 100).round(3)).round(0).to_string(index=False))


def print_ftp_pnl(r):
    max_err = r.ftp_monthly_df["identity_check"].abs().max()
    print(f"\n=== FTP / ALM Desk P&L (base scenario) === "
          f"(customer margin + ALM desk P&L reconciles to total NII, max abs error ${max_err:,.2f})")
    print(r.ftp_monthly_df[["month", "total_customer_margin", "alm_desk_pnl", "total_nii"]]
          .round(0).head(6).to_string(index=False))
    print("...")


def print_ftp_stability(r):
    print("\n=== ALM Desk P&L stability across rate scenarios (month 0 vs end of horizon) ===")
    for label, monthly in r.ftp_monthly_by_scenario.items():
        start_pnl = monthly.loc[monthly["month"] == 0, "alm_desk_pnl"].iloc[0]
        end_pnl = monthly.loc[monthly["month"] == monthly["month"].max(), "alm_desk_pnl"].iloc[0]
        print(f"  {label:>12}: month 0 = ${start_pnl:,.0f}/mo   ->   final month = ${end_pnl:,.0f}/mo")


def print_lcr(r):
    print(f"\n=== Liquidity Coverage Ratio (base scenario) === "
          f"(target: reg min {config.LCR_REGULATORY_MIN:.0%}, RAS {config.LCR_RAS_THRESHOLD:.0%}, "
          f"internal target {config.LCR_INTERNAL_TARGET:.0%})")
    for m in [0, r.months // 2, r.end_month]:
        row = r.base_lcr[r.base_lcr["month"] == m].iloc[0]
        print(f"  Month {m:>2}: LCR={row['lcr']:.1%}  (stressed: {row['lcr_stressed']:.1%})  "
              f"HQLA=${row['hqla']:,.0f}  net 30d outflows=${row['net_outflows']:,.0f}")


def print_nsfr(r):
    print(f"\n=== Net Stable Funding Ratio (base scenario) === (target: reg min {config.NSFR_REGULATORY_MIN:.0%})")
    for m in [0, r.months // 2, r.end_month]:
        row = r.base_nsfr[r.base_nsfr["month"] == m].iloc[0]
        print(f"  Month {m:>2}: NSFR={row['nsfr']:.1%}  ASF=${row['asf']:,.0f}  RSF=${row['rsf']:,.0f}")


def print_capital(r):
    print(f"\n=== CET1 Ratio (base scenario) === "
          f"(target: reg min {config.CET1_REGULATORY_MIN:.1%}, buffered min {config.CET1_BUFFERED_MIN:.1%}, "
          f"internal target {config.CET1_INTERNAL_TARGET:.1%}; dividend payout {config.DIVIDEND_PAYOUT_RATIO:.0%})")
    for m in [0, r.months // 2, r.end_month]:
        row = r.capital_df[r.capital_df["month"] == m].iloc[0]
        print(f"  Month {m:>2}: CET1 ratio={row['cet1_ratio']:.2%}  RWA=${row['rwa']:,.0f}  "
              f"CET1 capital=${row['cet1_capital']:,.0f}")


def print_joint_view(r):
    print("\n=== Joint LCR-NIM View (base scenario, month 0, ranked by margin per unit of liquidity cost) ===")
    ranked = r.joint_view_df.dropna(subset=["nim_per_unit_lcr_cost"]).sort_values(
        "nim_per_unit_lcr_cost", ascending=False
    )
    print(ranked[["position", "side", "nim_contribution", "ftp_customer_margin", "lcr_role", "lcr_impact",
                   "nim_per_unit_lcr_cost"]].round(4).to_string(index=False))


def print_mtm(r):
    print(f"\n=== AFS MTM Buffer (base scenario) === (capped at {config.TRADING_LIMIT_PCT:.0%} of HTM book)")
    for m in [0, r.months // 2, r.end_month]:
        row = r.mtm_summary_df[r.mtm_summary_df["month"] == m].iloc[0]
        print(f"  Month {m:>2}: unrealized gain=${row['total_unrealized_gain']:,.0f}  "
              f"buffer limit=${row['buffer_limit']:,.0f}  buffer available=${row['buffer_available']:,.0f}")


def print_backtest(r):
    if r.backtest_df is None:
        return
    print(f"\n=== Back-test vs {r.backtest_path} (base scenario) ===")
    print(r.backtest_df.round(2).head(6).to_string(index=False))
    print("...")
    print(f"  Cumulative NII error: ${r.backtest_df['nii_error'].sum():,.0f}  "
          f"(rate variance: ${r.backtest_df['rate_variance'].sum():,.0f}, "
          f"volume variance: ${r.backtest_df['volume_variance'].sum():,.0f}, "
          f"residual/unmodellable: ${r.backtest_df['residual_unmodellable'].sum():,.0f})")


def print_fdic_backtest(r):
    if r.backtest_fdic_quarters is None:
        return
    if r.fdic_backtest_message is not None:
        print(f"\n[main] {r.fdic_backtest_message}")
        return
    fdic_backtest_df, snapshot_fin = r.fdic_backtest_df, r.fdic_snapshot_fin
    print(f"\n=== Real-actuals back-test: {snapshot_fin.get('NAME')} (CERT {r.bank_cert}), "
          f"as-of {snapshot_fin.get('REPDTE')}, {r.backtest_fdic_quarters} quarters replayed "
          f"against the realized Treasury curve ===")
    print(fdic_backtest_df.round(2).to_string(index=False))
    print(f"  Cumulative NII error: ${fdic_backtest_df['nii_error'].sum():,.0f}  "
          f"(rate variance: ${fdic_backtest_df['rate_variance'].sum():,.0f}, "
          f"volume variance: ${fdic_backtest_df['volume_variance'].sum():,.0f}, "
          f"residual/unmodellable: ${fdic_backtest_df['residual_unmodellable'].sum():,.0f})")


def print_ftp_recalibration(r):
    if not r.ftp_recalibrate:
        return
    print("\n=== FTP policy spread calibration (minimizing cross-cycle ALM desk P&L variance) ===")
    print(f"  ALM desk P&L variance before calibration: {r.ftp_before_variance:,.0f}")
    print(f"  ALM desk P&L variance after calibration:  {r.ftp_after_variance:,.0f}")
    print("  Calibrated spread curve (tenor years -> spread):")
    for tenor, spread in sorted(r.ftp_calibrated_spreads.items()):
        print(f"    {tenor:>6.2f}y: {spread * 10000:6.1f} bps")


def print_output_paths(r):
    print(f"\nOutputs written to {r.output_dir.resolve()}")
    print("  - nim_forecast.xlsx (NIM, gap, duration/EVE, liquidity, earnings-at-risk, FTP/ALM P&L, LCR, NSFR,")
    print("    CET1 capital, joint LCR-NIM view, AFS MTM, back-test(s), bucket detail)")
    print("  - nim_by_scenario.png, base_yield_cost_spread.png, balance_sheet_mix.png,")
    print("    rate_sensitivity_gap.png, eve_sensitivity.png, structural_liquidity.png, ftp_alm_pnl.png,")
    print("    lcr_by_scenario.png, lcr_stressed.png, nsfr_by_scenario.png, cet1_by_scenario.png")
    print("  - dashboard.html (interactive, self-contained: open directly in a browser)")


def print_report(r):
    """Prints every report for a completed run, in the same order main.py used to."""
    if r.bank_cert_calibration_error is not None:
        print_bank_cert_calibration_error(r.bank_cert_calibration_error)
    if r.deposit_history_path is not None:
        print_deposit_history_estimates(r.deposit_history_path, r.deposit_history_log_rows)
    print_curve_info(r)
    print_nim_by_scenario(r)
    print_sensitivity(r)
    print_gap(r)
    print_liquidity(r)
    print_duration_and_eve(r)
    print_full_reval_eve(r)
    print_ear(r)
    print_ftp_pnl(r)
    print_ftp_stability(r)
    print_lcr(r)
    print_nsfr(r)
    print_capital(r)
    print_joint_view(r)
    print_mtm(r)
    print_backtest(r)
    print_fdic_backtest(r)
    print_ftp_recalibration(r)
