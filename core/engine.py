"""
Cohort-based monthly balance-sheet / NIM simulation engine.

Per-position mechanics live in core/products/ (one module per category_type:
variable, fixed_amortizing, administered, laddered) - this module seeds each
position's cohort/balance state, sequences the MCLR dependency, and enforces
the balance sheet identity.

All summary-level rates (asset yield, cost of funds, NIM) are computed on
average monthly balances (prev/new midpoint), never end-of-month balances,
so the rate and volume bases are consistent with each other and with the
interest dollars actually accrued that month (also avg-balance based).

MCLR is computed each month from liability positions flagged
feeds_mclr_deposit_cost plus the plug position's current rate, before any
MCLR-indexed position reprices that month:
  Stage 1: administered + laddered liabilities reprice (feeds MCLR's deposit-cost input).
  Stage 2: variable positions not indexed to MCLR reprice (feeds MCLR's borrowing-cost input).
  Stage 3: MCLR computed.
  Stage 4: MCLR-indexed variable positions + fixed_amortizing positions reprice.

Balance sheet identity (assets = liabilities + equity) is enforced from
month 1 onward: the `plug` position absorbs a funding shortfall, the
`cash_sink` position absorbs a surplus. Both must be `variable` positions
(core/balance_sheet.py validates this). Each month's adjustment nets
against the OTHER position's existing balance first (a surplus repays
outstanding plug borrowings before growing the cash sink; a shortfall draws
down existing sink cash before growing plug borrowings), so a scenario that
flips between deficit and surplus does not leave both growing side by side
forever.
"""

import copy

import numpy as np
import pandas as pd

import config
from core.indices import compute_mclr
from core.products import administered, fixed_amortizing, laddered, variable
from core.products.cohort import aggregate as _aggregate_cohorts


def _seed_cohorts(p, curve0):
    if p.category_type == "variable":
        return variable.seed(p, curve0)
    if p.category_type == "fixed_amortizing":
        return fixed_amortizing.seed(p, curve0)
    return []


def _step_variable_cohorts(p, cohorts, curve_t, t, mclr_t):
    variable.step(p, cohorts, curve_t, t, mclr_t)


def _step_fixed_amortizing_cohorts(p, cohorts, curve_t, t):
    fixed_amortizing.step(p, cohorts, curve_t, t)


def _step_administered(p, prev_balance, prev_rate, curve0, curve_lag, curve_t, t):
    return administered.step(p, prev_balance, prev_rate, curve0, curve_lag, curve_t, t)


def _step_laddered(p, prev_balance, prev_rate, curve_t, t):
    return laddered.step(p, prev_balance, prev_rate, curve_t, t)


def run_scenario(positions, curve_path, scenario_label="Base", initial_equity=None, retention_ratio=None):
    """
    Simulates one rate scenario across all positions for len(curve_path) months.
    Returns (monthly_summary_df, position_detail_df, cohort_detail_df).

    cohort_detail_df carries fixed_amortizing positions' per-vintage balances
    (month, bucket, origination_month, balance, rate) - the vintage mix needed
    for origination-locked FTP (core/ftp/matched_maturity.py), which position_detail_df's
    position-aggregated rows can't reconstruct on their own.
    """
    horizon = len(curve_path.curves)
    retention_ratio = config.RETENTION_RATIO if retention_ratio is None else retention_ratio

    if initial_equity:
        equity = initial_equity
    else:
        residual = sum(p.balance for p in positions if p.side == "asset") - \
            sum(p.balance for p in positions if p.side == "liability")
        equity = residual if residual > 0 else config.EQUITY_CAPITAL_FALLBACK

    plug_name = next(p.name for p in positions if p.plug)
    sink_name = next(p.name for p in positions if p.cash_sink)

    curve0 = curve_path.curves[0]
    admin_state = {p.name: {"balance": p.balance, "rate": p.rate} for p in positions if p.category_type == "administered"}
    ladder_state = {p.name: {"balance": p.balance, "rate": p.rate} for p in positions if p.category_type == "laddered"}
    cohort_state = {p.name: _seed_cohorts(p, curve0) for p in positions if p.category_type in ("variable", "fixed_amortizing")}

    prev_total_balance = {p.name: p.balance for p in positions}

    summary_rows = []
    detail_rows = []
    cohort_detail_rows = []
    prev_period_nii = 0.0

    for t in range(horizon):
        if t > 0:
            equity += retention_ratio * prev_period_nii

        curve_t = curve_path.curves[t]
        ladder_renewed = {}
        ladder_new_rate = {}

        if t > 0:
            # Stage 1: administered + laddered liabilities (deposit-cost inputs for MCLR).
            for p in positions:
                if p.category_type == "administered":
                    lag_idx = max(0, t - p.lag_months)
                    nb, nr = _step_administered(p, admin_state[p.name]["balance"], admin_state[p.name]["rate"],
                                                 curve0, curve_path.curves[lag_idx], curve_t, t)
                    admin_state[p.name] = {"balance": nb, "rate": nr}
                elif p.category_type == "laddered":
                    nb, nr, renewed, new_rate_piece = _step_laddered(
                        p, ladder_state[p.name]["balance"], ladder_state[p.name]["rate"], curve_t, t)
                    ladder_state[p.name] = {"balance": nb, "rate": nr}
                    ladder_renewed[p.name] = renewed
                    ladder_new_rate[p.name] = new_rate_piece

            # Stage 2: variable positions not indexed to MCLR (this determines the
            # current short-term borrowing rate MCLR needs).
            for p in positions:
                if p.category_type == "variable" and p.index != "MCLR":
                    _step_variable_cohorts(p, cohort_state[p.name], curve_t, t, mclr_t=None)

            # Stage 3: MCLR from positions flagged feeds_mclr_deposit_cost only
            # (excludes low-beta core CASA, which would otherwise understate it).
            deposit_balance = deposit_rate_x_balance = 0.0
            for p in positions:
                if p.side != "liability" or not p.feeds_mclr_deposit_cost:
                    continue
                if p.category_type == "administered":
                    b, r = admin_state[p.name]["balance"], admin_state[p.name]["rate"]
                    deposit_balance += b
                    deposit_rate_x_balance += b * r
                elif p.category_type == "laddered":
                    renewed, new_rate_piece = ladder_renewed[p.name], ladder_new_rate[p.name]
                    deposit_balance += renewed
                    deposit_rate_x_balance += renewed * new_rate_piece
            new_deposit_weighted_rate = (deposit_rate_x_balance / deposit_balance) if deposit_balance else None

            borrowing_rate = None
            for p in positions:
                if p.plug:
                    _, borrowing_rate = _aggregate_cohorts(cohort_state[p.name])

            mclr = compute_mclr(new_deposit_weighted_rate, borrowing_rate, curve_t.spot(1 / 12))

            # Stage 4: MCLR-indexed variable positions + fixed_amortizing assets.
            for p in positions:
                if p.category_type == "variable" and p.index == "MCLR":
                    _step_variable_cohorts(p, cohort_state[p.name], curve_t, t, mclr_t=mclr)
                elif p.category_type == "fixed_amortizing":
                    _step_fixed_amortizing_cohorts(p, cohort_state[p.name], curve_t, t)

        # Aggregate every position to (balance, rate) for this month.
        new_balances, new_rates = {}, {}
        for p in positions:
            if p.category_type == "administered":
                new_balances[p.name], new_rates[p.name] = admin_state[p.name]["balance"], admin_state[p.name]["rate"]
            elif p.category_type == "laddered":
                new_balances[p.name], new_rates[p.name] = ladder_state[p.name]["balance"], ladder_state[p.name]["rate"]
            else:
                new_balances[p.name], new_rates[p.name] = _aggregate_cohorts(cohort_state[p.name])

        # Balance sheet identity (month 1+): plug/cash_sink are variable positions,
        # so the adjustment lands directly on their cohort list, then re-aggregates.
        # Net against the opposite position's existing balance first (see module
        # docstring) so the two don't both grow forever once the gap flips sign.
        if t > 0:
            total_assets = sum(new_balances[p.name] for p in positions if p.side == "asset")
            total_liab = sum(new_balances[p.name] for p in positions if p.side == "liability")
            funding_gap = total_assets - total_liab - equity

            plug_balance = cohort_state[plug_name][0].balance
            sink_balance = cohort_state[sink_name][0].balance

            if funding_gap >= 0:
                sink_paydown = min(sink_balance, funding_gap)
                cohort_state[sink_name][0].balance -= sink_paydown
                cohort_state[plug_name][0].balance += funding_gap - sink_paydown
            else:
                surplus = -funding_gap
                plug_paydown = min(plug_balance, surplus)
                cohort_state[plug_name][0].balance -= plug_paydown
                cohort_state[sink_name][0].balance += surplus - plug_paydown

            new_balances[plug_name], new_rates[plug_name] = _aggregate_cohorts(cohort_state[plug_name])
            new_balances[sink_name], new_rates[sink_name] = _aggregate_cohorts(cohort_state[sink_name])

        for p in positions:
            if p.category_type == "fixed_amortizing":
                for c in cohort_state[p.name]:
                    cohort_detail_rows.append({
                        "scenario": scenario_label, "month": t, "bucket": p.name,
                        "origination_month": c.origination_month, "balance": c.balance, "rate": c.rate,
                    })

        period_ii = period_ie = period_avg_earning_assets = period_avg_ib_liabilities = 0.0

        for p in positions:
            prev_balance = prev_total_balance[p.name]
            new_balance = new_balances[p.name]
            new_rate = new_rates[p.name]

            avg_balance = (prev_balance + new_balance) / 2 if t > 0 else prev_balance
            monthly_interest = avg_balance * new_rate / 12

            if p.side == "asset":
                period_ii += monthly_interest
                period_avg_earning_assets += avg_balance
            else:
                period_ie += monthly_interest
                period_avg_ib_liabilities += avg_balance

            detail_rows.append({
                "scenario": scenario_label, "month": t, "bucket": p.name, "side": p.side,
                "balance": new_balance, "rate": new_rate, "interest": monthly_interest,
            })
            prev_total_balance[p.name] = new_balance

        nim_annualized = (period_ii - period_ie) * 12 / period_avg_earning_assets if period_avg_earning_assets else np.nan
        asset_yield = period_ii * 12 / period_avg_earning_assets if period_avg_earning_assets else np.nan
        cost_of_funds = period_ie * 12 / period_avg_ib_liabilities if period_avg_ib_liabilities else np.nan

        summary_rows.append({
            "scenario": scenario_label,
            "month": t,
            "benchmark_rate": curve_t.spot(1 / 12),
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

    return pd.DataFrame(summary_rows), pd.DataFrame(detail_rows), pd.DataFrame(cohort_detail_rows)


def run_all_scenarios(positions, curve_paths: dict, initial_equity=None, retention_ratio=None):
    """
    curve_paths: {label: curve.scenarios.CurvePath}.
    Returns (combined_summary_df, {label: detail_df}, {label: cohort_detail_df}).
    """
    summaries = []
    details = {}
    cohort_details = {}
    for label, curve_path in curve_paths.items():
        summary_df, detail_df, cohort_detail_df = run_scenario(
            copy.deepcopy(positions), curve_path, scenario_label=label,
            initial_equity=initial_equity, retention_ratio=retention_ratio,
        )
        summaries.append(summary_df)
        details[label] = detail_df
        cohort_details[label] = cohort_detail_df
    return pd.concat(summaries, ignore_index=True), details, cohort_details
