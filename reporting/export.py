import pandas as pd


def export_excel(out_path, combined_summary_df, details_by_scenario, sensitivity_df=None,
                  gap_df=None, duration_df=None, duration_summary=None, eve_df=None,
                  liquidity_df=None, ear_df=None, ftp_monthly_df=None, ftp_detail_df=None,
                  lcr_df=None, joint_view_df=None, mtm_detail_df=None, mtm_summary_df=None,
                  full_reval_eve_df=None, backtest_df=None, fdic_backtest_df=None,
                  nsfr_df=None, capital_df=None):
    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        pivot = combined_summary_df.pivot(index="month", columns="scenario", values="nim") * 100
        pivot.round(3).to_excel(writer, sheet_name="NIM Summary (%)")

        combined_summary_df.round(6).to_excel(writer, sheet_name="Monthly Detail", index=False)

        if sensitivity_df is not None:
            sensitivity_df.to_excel(writer, sheet_name="Sensitivity", index=False)

        if gap_df is not None:
            gap_df.round(2).to_excel(writer, sheet_name="Rate Sensitivity Gap", index=False)

        if duration_df is not None:
            duration_df.round(4).to_excel(writer, sheet_name="Duration Detail", index=False)

        if duration_summary is not None:
            pd.DataFrame([duration_summary]).round(4).to_excel(writer, sheet_name="Duration Gap Summary", index=False)

        if eve_df is not None:
            eve_df.round(4).to_excel(writer, sheet_name="EVE Sensitivity", index=False)

        if liquidity_df is not None:
            liquidity_df.round(4).to_excel(writer, sheet_name="Structural Liquidity", index=False)

        if ear_df is not None:
            ear_df.round(2).to_excel(writer, sheet_name="Earnings at Risk", index=False)

        if ftp_monthly_df is not None:
            ftp_monthly_df.round(2).to_excel(writer, sheet_name="FTP - ALM Desk PnL", index=False)

        if ftp_detail_df is not None:
            ftp_detail_df.round(2).to_excel(writer, sheet_name="FTP - Bucket Detail", index=False)

        if lcr_df is not None:
            lcr_df.round(4).to_excel(writer, sheet_name="LCR", index=False)

        if joint_view_df is not None:
            joint_view_df.round(4).to_excel(writer, sheet_name="Joint LCR-NIM View", index=False)

        if mtm_detail_df is not None:
            mtm_detail_df.round(2).to_excel(writer, sheet_name="AFS MTM Detail", index=False)

        if mtm_summary_df is not None:
            mtm_summary_df.round(2).to_excel(writer, sheet_name="AFS MTM Buffer", index=False)

        if full_reval_eve_df is not None:
            full_reval_eve_df.round(2).to_excel(writer, sheet_name="EVE Full Revaluation", index=False)

        if backtest_df is not None:
            backtest_df.round(2).to_excel(writer, sheet_name="Backtest vs Actuals", index=False)

        if fdic_backtest_df is not None:
            fdic_backtest_df.round(2).to_excel(writer, sheet_name="FDIC Real-Actuals Backtest", index=False)

        if nsfr_df is not None:
            nsfr_df.round(4).to_excel(writer, sheet_name="NSFR", index=False)

        if capital_df is not None:
            capital_df.round(4).to_excel(writer, sheet_name="CET1 Capital", index=False)

        for label, detail_df in details_by_scenario.items():
            sheet = f"Buckets - {label}"[:31]
            detail_df.round(2).to_excel(writer, sheet_name=sheet, index=False)


def export_from_results(r):
    """export_excel, fed from a completed pipeline.RunResults."""
    export_excel(
        r.output_dir / "nim_forecast.xlsx", r.combined_summary, r.details_by_scenario, r.sensitivity_df,
        gap_df=r.gap_df, duration_df=r.duration_df, duration_summary=r.duration_summary,
        eve_df=r.eve_df, liquidity_df=r.liquidity_df, ear_df=r.ear_df,
        ftp_monthly_df=r.ftp_monthly_df, ftp_detail_df=r.ftp_detail_df,
        lcr_df=r.lcr_df, joint_view_df=r.joint_view_df, mtm_detail_df=r.mtm_detail_df, mtm_summary_df=r.mtm_summary_df,
        full_reval_eve_df=r.full_reval_eve_df, backtest_df=r.backtest_df, fdic_backtest_df=r.fdic_backtest_df,
        nsfr_df=r.nsfr_df, capital_df=r.capital_df,
    )
