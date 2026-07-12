import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker


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
