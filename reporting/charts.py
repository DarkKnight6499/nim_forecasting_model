import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker

import config


def plot_nim_by_scenario(combined_summary_df, out_path):
    fig, ax = plt.subplots(figsize=(10, 6))
    for label, grp in combined_summary_df.groupby("scenario"):
        ax.plot(grp["month"], grp["nim"] * 100, marker="o", markersize=3, label=label)
    ax.set_xlabel("Month")
    ax.set_ylabel("NIM (annualized, %)")
    ax.set_title("Net Interest Margin Forecast by Rate Scenario")
    ax.yaxis.set_major_formatter(mticker.FormatStrFormatter("%.2f%%"))
    ax.legend()
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def plot_yield_cost_spread(base_summary_df, out_path):
    fig, ax = plt.subplots(figsize=(10, 6))
    ax.plot(base_summary_df["month"], base_summary_df["yield_on_earning_assets"] * 100, label="Yield on Earning Assets")
    ax.plot(base_summary_df["month"], base_summary_df["cost_of_ib_liabilities"] * 100, label="Cost of IB Liabilities")
    ax.plot(base_summary_df["month"], base_summary_df["nim"] * 100, label="NIM", linestyle="--")
    ax.set_xlabel("Month")
    ax.set_ylabel("Rate (%)")
    ax.set_title("Base Scenario: Yield, Cost of Funds, and NIM")
    ax.legend()
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def plot_balance_sheet_mix(bucket_detail_df, out_path, month=0):
    snap = bucket_detail_df[bucket_detail_df["month"] == month]
    fig, axes = plt.subplots(1, 2, figsize=(12, 6))
    for ax, side in zip(axes, ["asset", "liability"]):
        s = snap[snap["side"] == side].set_index("bucket")["balance"]
        ax.pie(s, labels=s.index, autopct="%1.0f%%", textprops={"fontsize": 8})
        ax.set_title(f"{side.capitalize()} mix (month {month})")
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def plot_rate_sensitivity_gap(gap_df, out_path):
    fig, ax1 = plt.subplots(figsize=(11, 6))
    x = range(len(gap_df))
    width = 0.35
    ax1.bar([i - width / 2 for i in x], gap_df["RSA"] / 1e6, width, label="RSA (repricing assets)")
    ax1.bar([i + width / 2 for i in x], gap_df["RSL"] / 1e6, width, label="RSL (repricing liabilities)")
    ax1.set_xticks(list(x))
    ax1.set_xticklabels(gap_df["band"])
    ax1.set_ylabel("$ millions")
    ax1.set_xlabel("Repricing time band")
    ax1.set_title("Interest Rate Sensitivity Gap")
    ax1.legend(loc="upper left")
    ax1.grid(alpha=0.3, axis="y")

    ax2 = ax1.twinx()
    ax2.plot(list(x), gap_df["cumulative_gap"] / 1e6, color="black", marker="o", linewidth=2, label="Cumulative gap")
    ax2.axhline(0, color="gray", linewidth=0.8)
    ax2.set_ylabel("Cumulative gap ($ millions)")
    ax2.legend(loc="upper right")

    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def plot_eve_sensitivity(eve_df, out_path):
    fig, ax = plt.subplots(figsize=(9, 6))
    colors = ["#d62728" if v < 0 else "#2ca02c" for v in eve_df["delta_eve_pct_equity"]]
    ax.bar(eve_df["scenario"], eve_df["delta_eve_pct_equity"] * 100, color=colors)
    ax.axhline(0, color="black", linewidth=0.8)
    ax.set_ylabel("Delta EVE (% of equity capital)")
    ax.set_title("Economic Value of Equity (EVE) Sensitivity")
    ax.yaxis.set_major_formatter(mticker.FormatStrFormatter("%.1f%%"))
    ax.grid(alpha=0.3, axis="y")
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def plot_earnings_at_risk(monthly_delta_df, out_path):
    fig, ax = plt.subplots(figsize=(10, 6))
    for label, grp in monthly_delta_df.groupby("scenario"):
        if label == "Base (flat)":
            continue
        ax.plot(grp["month"], grp["nii_delta"] / 1e6, marker="o", markersize=3, label=label)
    ax.axhline(0, color="black", linewidth=0.8)
    ax.set_xlabel("Month")
    ax.set_ylabel("NII delta vs. base ($ millions)")
    ax.set_title("Earnings-at-Risk: Monthly NII Impact by Scenario")
    ax.legend()
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def plot_ftp_alm_pnl(ftp_monthly_df, out_path):
    fig, ax = plt.subplots(figsize=(10, 6))
    ax.stackplot(
        ftp_monthly_df["month"],
        ftp_monthly_df["total_customer_margin"] / 1e6,
        ftp_monthly_df["alm_desk_pnl"] / 1e6,
        labels=["Customer margin (business units)", "ALM/Treasury desk P&L (FTP net)"],
        alpha=0.85,
    )
    ax.plot(ftp_monthly_df["month"], ftp_monthly_df["total_nii"] / 1e6, color="black",
             linewidth=1.5, linestyle="--", label="Total NII (check)")
    ax.set_xlabel("Month")
    ax.set_ylabel("$ millions / month")
    ax.set_title("FTP Split: Customer Margin vs. ALM/Treasury Desk P&L")
    ax.legend(loc="upper left", fontsize=8)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def plot_lcr_by_scenario(lcr_df, out_path, regulatory_min=None, ras_threshold=None, internal_target=None):
    fig, ax = plt.subplots(figsize=(10, 6))
    for label, grp in lcr_df.groupby("scenario"):
        ax.plot(grp["month"], grp["lcr"] * 100, marker="o", markersize=3, label=label)
    if regulatory_min is not None:
        ax.axhline(regulatory_min * 100, color="red", linestyle="--", linewidth=1, label="Regulatory minimum")
    if ras_threshold is not None:
        ax.axhline(ras_threshold * 100, color="orange", linestyle="--", linewidth=1, label="RAS threshold")
    if internal_target is not None:
        ax.axhline(internal_target * 100, color="green", linestyle="--", linewidth=1, label="Internal target")
    ax.set_xlabel("Month")
    ax.set_ylabel("LCR (%)")
    ax.set_title("Liquidity Coverage Ratio by Rate Scenario")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def plot_lcr_stressed(base_lcr_df, out_path, regulatory_min=None, ras_threshold=None, internal_target=None):
    """Base scenario only: base vs. scenario-stressed LCR against threshold lines."""
    fig, ax = plt.subplots(figsize=(10, 6))
    ax.plot(base_lcr_df["month"], base_lcr_df["lcr"] * 100, marker="o", markersize=3, label="LCR (base)")
    ax.plot(base_lcr_df["month"], base_lcr_df["lcr_stressed"] * 100, marker="o", markersize=3,
            linestyle="--", label="LCR (stressed)")
    if regulatory_min is not None:
        ax.axhline(regulatory_min * 100, color="red", linestyle="--", linewidth=1, label="Regulatory minimum")
    if ras_threshold is not None:
        ax.axhline(ras_threshold * 100, color="orange", linestyle="--", linewidth=1, label="RAS threshold")
    if internal_target is not None:
        ax.axhline(internal_target * 100, color="green", linestyle="--", linewidth=1, label="Internal target")
    ax.set_xlabel("Month")
    ax.set_ylabel("LCR (%)")
    ax.set_title("Liquidity Coverage Ratio: Base vs. Stressed Outflow Assumptions (base rate scenario)")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def plot_nsfr_by_scenario(nsfr_df, out_path, regulatory_min=None):
    fig, ax = plt.subplots(figsize=(10, 6))
    for label, grp in nsfr_df.groupby("scenario"):
        ax.plot(grp["month"], grp["nsfr"] * 100, marker="o", markersize=3, label=label)
    if regulatory_min is not None:
        ax.axhline(regulatory_min * 100, color="red", linestyle="--", linewidth=1, label="Regulatory minimum")
    ax.set_xlabel("Month")
    ax.set_ylabel("NSFR (%)")
    ax.set_title("Net Stable Funding Ratio by Rate Scenario")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def plot_cet1_by_scenario(capital_df, out_path, regulatory_min=None, buffered_min=None, internal_target=None):
    fig, ax = plt.subplots(figsize=(10, 6))
    for label, grp in capital_df.groupby("scenario"):
        ax.plot(grp["month"], grp["cet1_ratio"] * 100, marker="o", markersize=3, label=label)
    if regulatory_min is not None:
        ax.axhline(regulatory_min * 100, color="red", linestyle="--", linewidth=1, label="Regulatory minimum")
    if buffered_min is not None:
        ax.axhline(buffered_min * 100, color="orange", linestyle="--", linewidth=1, label="Buffered minimum")
    if internal_target is not None:
        ax.axhline(internal_target * 100, color="green", linestyle="--", linewidth=1, label="Internal target")
    ax.set_xlabel("Month")
    ax.set_ylabel("CET1 ratio (%)")
    ax.set_title("CET1 Ratio by Rate Scenario")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def plot_liquidity_gap(liquidity_df, out_path, tolerance_pct=None):
    fig, ax1 = plt.subplots(figsize=(11, 6))
    x = range(len(liquidity_df))
    width = 0.35
    ax1.bar([i - width / 2 for i in x], liquidity_df["inflows"] / 1e6, width, label="Inflows (assets)")
    ax1.bar([i + width / 2 for i in x], liquidity_df["outflows"] / 1e6, width, label="Outflows (liabilities)")
    ax1.set_xticks(list(x))
    ax1.set_xticklabels(liquidity_df["band"])
    ax1.set_ylabel("$ millions")
    ax1.set_xlabel("Time band")
    ax1.set_title("Structural Liquidity Statement")
    ax1.legend(loc="upper left")
    ax1.grid(alpha=0.3, axis="y")

    ax2 = ax1.twinx()
    ax2.plot(list(x), liquidity_df["cumulative_gap_pct_assets"] * 100, color="black", marker="o",
             linewidth=2, label="Cumulative gap (% of assets)")
    if tolerance_pct is not None:
        ax2.axhline(tolerance_pct * 100, color="red", linestyle="--", linewidth=1, label="Policy tolerance")
    ax2.axhline(0, color="gray", linewidth=0.8)
    ax2.set_ylabel("Cumulative gap (% of total assets)")
    ax2.legend(loc="upper right")

    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def generate_all(r):
    """Generates every chart for a completed pipeline.RunResults, into r.output_dir."""
    out_dir = r.output_dir
    plot_nim_by_scenario(r.combined_summary, out_dir / "nim_by_scenario.png")
    plot_yield_cost_spread(r.base_summary, out_dir / "base_yield_cost_spread.png")
    plot_balance_sheet_mix(r.details_by_scenario[r.base_label], out_dir / "balance_sheet_mix.png", month=0)
    plot_rate_sensitivity_gap(r.gap_df, out_dir / "rate_sensitivity_gap.png")
    plot_eve_sensitivity(r.eve_df, out_dir / "eve_sensitivity.png")
    plot_liquidity_gap(r.liquidity_df, out_dir / "structural_liquidity.png",
                        tolerance_pct=config.LIQUIDITY_GAP_TOLERANCE_PCT_ASSETS)
    plot_earnings_at_risk(r.nii_delta_df, out_dir / "earnings_at_risk.png")
    plot_ftp_alm_pnl(r.ftp_monthly_df, out_dir / "ftp_alm_pnl.png")
    plot_lcr_by_scenario(r.lcr_df, out_dir / "lcr_by_scenario.png", regulatory_min=config.LCR_REGULATORY_MIN,
                          ras_threshold=config.LCR_RAS_THRESHOLD, internal_target=config.LCR_INTERNAL_TARGET)
    plot_lcr_stressed(r.base_lcr, out_dir / "lcr_stressed.png", regulatory_min=config.LCR_REGULATORY_MIN,
                       ras_threshold=config.LCR_RAS_THRESHOLD, internal_target=config.LCR_INTERNAL_TARGET)
    plot_nsfr_by_scenario(r.nsfr_df, out_dir / "nsfr_by_scenario.png", regulatory_min=config.NSFR_REGULATORY_MIN)
    plot_cet1_by_scenario(r.capital_df, out_dir / "cet1_by_scenario.png", regulatory_min=config.CET1_REGULATORY_MIN,
                           buffered_min=config.CET1_BUFFERED_MIN, internal_target=config.CET1_INTERNAL_TARGET)
