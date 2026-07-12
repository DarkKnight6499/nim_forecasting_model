"""
Monthly dynamic balance-sheet / NIM simulation engine.

For each position, balance and rate evolve month-to-month according to its
repricing behavior (`category_type`):

  variable        - reprices with the benchmark, scaled by `beta`, on a fixed
                    cadence (`reset_frequency_months`; default 1 = every
                    month). Between reset dates the rate is flat, then jumps
                    to catch up - mirrors external-benchmark-linked floating
                    loans that reset quarterly/semi-annually rather than
                    continuously (e.g. RLLR/MCLR-style reset tenors). Used
                    for prime/SOFR-linked loans, short-term borrowings, fed
                    funds sold. (A future iteration should replace the whole-book
                    synchronized reset this implies with a staggered cohort schedule.)
  administered     - like variable (reset_frequency_months=1 implicitly, via
                    lag_months), but the bank controls the rate and moves it
                    slowly/partially (deposit beta) with a lag. Used for
                    NOW/savings/MMDA. If `seasonal=True`, monthly growth is
                    scaled by config.SEASONALITY_INDEX_DEPOSITS.
  fixed_amortizing - balance runs off via a constant prepayment/amortization
                    rate (CPR); what runs off (plus organic growth) is
                    replaced by new production priced at benchmark + spread.
                    The position's blended rate drifts as old fixed-rate volume
                    is replaced. Used for term loans (CRE, resi, consumer).
  laddered         - a fraction (1/ladder_months) of the balance matures each
                    month and is renewed at benchmark + spread. Used for
                    securities, CDs, term borrowings.

Each month, interest income/expense = avg(balance) * rate / 12, summed by
side. NIM is annualized: (interest income - interest expense) * 12 /
avg(total earning assets).

Balance sheet identity: from month 1 onward (month 0 is reported exactly as
calibrated/configured, to preserve the FDIC day-0 NIM-matching invariant -
see data_sources/fdic_bank.py), assets must equal liabilities plus equity
every month. One designated `plug` position (short-term borrowings) absorbs
a funding shortfall; one designated `cash_sink` position (fed funds sold)
absorbs a surplus. Equity grows each month by `config.RETENTION_RATIO` times
the prior month's net interest income.
"""

import copy
import numpy as np
import pandas as pd

import config


def _monthly_from_annual_rate(annual_rate):
    """Converts an annual constant-attrition/growth rate to its monthly equivalent."""
    return 1 - (1 - annual_rate) ** (1 / 12) if annual_rate < 1 else annual_rate / 12


def _step_position(p, prev_balance, prev_rate, benchmark_path, t):
    benchmark_t = benchmark_path[t]
    benchmark_0 = benchmark_path[0]

    if p.category_type == "variable":
        if t % p.reset_frequency_months == 0:
            delta = benchmark_t - benchmark_0
            new_rate = p.rate + p.beta * delta
            if p.rate_floor is not None:
                new_rate = max(new_rate, p.rate_floor)
        else:
            new_rate = prev_rate  # holds flat between reset dates
        growth_m = p.growth_rate_annual / 12
        new_balance = prev_balance * (1 + growth_m)
        return new_balance, new_rate

    if p.category_type == "administered":
        lag_idx = max(0, t - p.lag_months)
        delta = benchmark_path[lag_idx] - benchmark_0
        new_rate = p.rate + p.beta * delta
        if p.rate_floor is not None:
            new_rate = max(new_rate, p.rate_floor)
        new_rate = max(new_rate, 0.0)
        growth_m = p.growth_rate_annual / 12
        if p.seasonal:
            growth_m *= config.SEASONALITY_INDEX_DEPOSITS[t % 12]
        new_balance = prev_balance * (1 + growth_m)
        return new_balance, new_rate

    if p.category_type == "fixed_amortizing":
        cpr_m = _monthly_from_annual_rate(p.cpr_annual)
        runoff = prev_balance * cpr_m
        surviving = prev_balance - runoff
        growth = prev_balance * (p.growth_rate_annual / 12)
        new_production = runoff + max(0.0, growth)
        new_prod_rate = benchmark_t + p.spread
        if p.rate_floor is not None:
            new_prod_rate = max(new_prod_rate, p.rate_floor)
        new_balance = surviving + new_production
        if new_balance <= 0:
            return 0.0, new_prod_rate
        blended_rate = (surviving * prev_rate + new_production * new_prod_rate) / new_balance
        return new_balance, blended_rate

    if p.category_type == "laddered":
        maturing = prev_balance / p.ladder_months
        growth = prev_balance * (p.growth_rate_annual / 12)
        renewed = maturing + max(0.0, growth)
        new_rate_piece = benchmark_t + p.spread
        if p.rate_floor is not None:
            new_rate_piece = max(new_rate_piece, p.rate_floor)
        surviving = prev_balance - maturing
        new_balance = surviving + renewed
        if new_balance <= 0:
            return 0.0, new_rate_piece
        blended_rate = (surviving * prev_rate + renewed * new_rate_piece) / new_balance
        return new_balance, blended_rate

    raise ValueError(f"Unknown category_type: {p.category_type}")


def run_scenario(positions, benchmark_path, scenario_label="Base", initial_equity=None, retention_ratio=None):
    """
    Simulates one rate scenario across all positions for len(benchmark_path) months.
    Returns (monthly_summary_df, position_detail_df).
    """
    horizon = len(benchmark_path)
    retention_ratio = config.RETENTION_RATIO if retention_ratio is None else retention_ratio
    if initial_equity:
        equity = initial_equity
    else:
        # Same convention as alm_reports.compute_duration_gap's equity fallback: the
        # day-0 residual (assets - liabilities), so the identity holds exactly from
        # month 0 with no artificial day-1 jump when no real equity figure is given.
        residual = sum(p.balance for p in positions if p.side == "asset") - \
            sum(p.balance for p in positions if p.side == "liability")
        equity = residual if residual > 0 else config.EQUITY_CAPITAL_FALLBACK

    plug_name = next(p.name for p in positions if p.plug)
    sink_name = next(p.name for p in positions if p.cash_sink)

    state = {p.name: {"balance": p.balance, "rate": p.rate, "side": p.side} for p in positions}

    summary_rows = []
    detail_rows = []
    prev_period_nii = 0.0

    for t in range(horizon):
        if t > 0:
            equity += retention_ratio * prev_period_nii

        new_balances = {}
        new_rates = {}
        for p in positions:
            prev_balance = state[p.name]["balance"]
            prev_rate = state[p.name]["rate"]
            if t == 0:
                nb, nr = prev_balance, prev_rate
            else:
                nb, nr = _step_position(p, prev_balance, prev_rate, benchmark_path, t)
            new_balances[p.name] = nb
            new_rates[p.name] = nr

        # Balance sheet identity (assets = liabilities + equity) is enforced from
        # month 1 onward only. Month 0 is reported exactly as calibrated/configured,
        # so the FDIC day-0 NIM-matching invariant (data_sources/fdic_bank.py) is
        # never perturbed by the plug mechanic.
        if t > 0:
            total_assets = sum(new_balances[p.name] for p in positions if p.side == "asset")
            total_liab = sum(new_balances[p.name] for p in positions if p.side == "liability")
            funding_gap = total_assets - total_liab - equity
            if funding_gap >= 0:
                new_balances[plug_name] += funding_gap
            else:
                new_balances[sink_name] += -funding_gap

        period_ii = 0.0
        period_ie = 0.0
        period_avg_earning_assets = 0.0

        for p in positions:
            prev_balance = state[p.name]["balance"]
            new_balance = new_balances[p.name]
            new_rate = new_rates[p.name]

            avg_balance = (prev_balance + new_balance) / 2 if t > 0 else prev_balance
            monthly_interest = avg_balance * new_rate / 12

            if p.side == "asset":
                period_ii += monthly_interest
                period_avg_earning_assets += avg_balance
            else:
                period_ie += monthly_interest

            state[p.name]["balance"] = new_balance
            state[p.name]["rate"] = new_rate

            detail_rows.append({
                "scenario": scenario_label, "month": t, "bucket": p.name, "side": p.side,
                "balance": new_balance, "rate": new_rate, "interest": monthly_interest,
            })

        nim_annualized = (period_ii - period_ie) * 12 / period_avg_earning_assets if period_avg_earning_assets else np.nan
        asset_yield = period_ii * 12 / period_avg_earning_assets if period_avg_earning_assets else np.nan
        total_ib_liab = sum(state[p.name]["balance"] for p in positions if p.side == "liability")
        cost_of_funds = period_ie * 12 / total_ib_liab if total_ib_liab else np.nan

        summary_rows.append({
            "scenario": scenario_label,
            "month": t,
            "benchmark_rate": benchmark_path[t],
            "interest_income": period_ii,
            "interest_expense": period_ie,
            "net_interest_income": period_ii - period_ie,
            "avg_earning_assets": period_avg_earning_assets,
            "yield_on_earning_assets": asset_yield,
            "cost_of_ib_liabilities": cost_of_funds,
            "net_interest_spread": asset_yield - cost_of_funds if pd.notna(asset_yield) and pd.notna(cost_of_funds) else np.nan,
            "nim": nim_annualized,
            "equity": equity,
        })

        prev_period_nii = period_ii - period_ie

    return pd.DataFrame(summary_rows), pd.DataFrame(detail_rows)


def run_all_scenarios(positions, scenario_paths: dict, initial_equity=None, retention_ratio=None):
    """scenario_paths: {label: benchmark_path array}. Returns (combined_summary_df, {label: detail_df})."""
    summaries = []
    details = {}
    for label, path in scenario_paths.items():
        summary_df, detail_df = run_scenario(
            copy.deepcopy(positions), path, scenario_label=label,
            initial_equity=initial_equity, retention_ratio=retention_ratio,
        )
        summaries.append(summary_df)
        details[label] = detail_df
    return pd.concat(summaries, ignore_index=True), details
