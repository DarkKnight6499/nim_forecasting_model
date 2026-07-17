"""
Estimates NMD (non-maturity deposit) behavioral parameters from an actual
balance history, instead of assuming them.

core_fraction: the lowest ratio, anywhere in the series, of the trailing
12-month minimum balance to the current balance - the point where the
trailing window still holds an old low (core-only) reading while the current
balance has already been topped back up, which is the cleanest single read
on "how much of this book is always there."

decay_annual_core: fitted off the trailing-minimum series' own plateaus (the
months where that minimum is being held, i.e. one reading per replenishment
cycle) rather than its interior, since within a cycle the trailing minimum
just echoes the current balance's own (much faster) non-core decline -
comparing plateau to plateau isolates the slow-moving core trend from that.

decay_annual_noncore: fitted off the volatile residual (current balance
minus the trailing minimum), pooling every declining run (the interval
between one replenishment and the next) into a single regression against
time-since-that-run-started, so a replenished top-up doesn't flatten the
apparent slope.
"""

import numpy as np
import pandas as pd


def apply_estimates_from_csv(positions, csv_path) -> list:
    """
    csv_path: CSV with columns month, product, balance. For each product with
    a matching "{product} - Core" / "{product} - Non-Core" pair in positions,
    overrides their behavioral_duration_years and liquidity_decay_annual with
    estimate_decay's output. Returns a log of assumed-vs-estimated rows for printing.
    """
    history = pd.read_csv(csv_path)
    log_rows = []

    for product, group in history.groupby("product"):
        balance_series = group.sort_values("month")["balance"]
        if len(balance_series) < 12:
            continue

        core_pos = next((p for p in positions if p.name == f"{product} - Core"), None)
        noncore_pos = next((p for p in positions if p.name == f"{product} - Non-Core"), None)
        if core_pos is None or noncore_pos is None:
            continue

        estimate = estimate_decay(balance_series)
        core_avg_life = 1.0 / estimate["decay_annual_core"] if estimate["decay_annual_core"] > 0 else core_pos.behavioral_duration_years
        noncore_avg_life = 1.0 / estimate["decay_annual_noncore"] if estimate["decay_annual_noncore"] > 0 else noncore_pos.behavioral_duration_years

        for pos, param, assumed, estimated in [
            (core_pos, "behavioral_duration_years", core_pos.behavioral_duration_years, core_avg_life),
            (core_pos, "liquidity_decay_annual", core_pos.liquidity_decay_annual, estimate["decay_annual_core"]),
            (noncore_pos, "behavioral_duration_years", noncore_pos.behavioral_duration_years, noncore_avg_life),
            (noncore_pos, "liquidity_decay_annual", noncore_pos.liquidity_decay_annual, estimate["decay_annual_noncore"]),
        ]:
            log_rows.append({"position": pos.name, "param": param, "assumed": assumed, "estimated": estimated})

        core_pos.behavioral_duration_years = core_avg_life
        core_pos.liquidity_decay_annual = estimate["decay_annual_core"]
        noncore_pos.behavioral_duration_years = noncore_avg_life
        noncore_pos.liquidity_decay_annual = estimate["decay_annual_noncore"]

    return log_rows


def _fit_pooled_decay(values: np.ndarray, reset_ratio: float) -> float:
    """
    Monthly decay rate from a log-linear regression pooling every declining
    run in `values` (a run resets whenever a value exceeds the previous one
    by more than reset_ratio), each measured against months since its own
    run start.
    """
    run_start = 0
    months_since_start, log_values = [], []
    for i, v in enumerate(values):
        if i > 0 and v > values[i - 1] * (1 + reset_ratio):
            run_start = i
        if v > 0:
            months_since_start.append(i - run_start)
            log_values.append(np.log(v))
    if len(months_since_start) < 2:
        return 0.0
    slope, _ = np.polyfit(months_since_start, log_values, 1)
    return float(np.clip(1 - np.exp(slope), 0.0, 1.0))


def _fit_plateau_decay(rolling_min: np.ndarray) -> float:
    """Monthly decay rate from a log-linear regression of rolling_min's distinct held values against their month."""
    plateau_months, plateau_values = [], []
    for i, v in enumerate(rolling_min):
        if i == 0 or v != rolling_min[i - 1]:
            plateau_months.append(i)
            plateau_values.append(v)
    if len(plateau_values) < 2:
        return 0.0
    slope, _ = np.polyfit(plateau_months, np.log(plateau_values), 1)
    return float(np.clip(1 - np.exp(slope), 0.0, 1.0))


def estimate_decay(balance_series) -> dict:
    """
    balance_series: monthly balances for one product, oldest first.
    Returns {"core_fraction", "decay_annual_core", "decay_annual_noncore", "avg_life_years"}.
    """
    balance = pd.Series(balance_series).reset_index(drop=True)
    rolling_min = balance.rolling(window=12, min_periods=1).min()

    ratio = (rolling_min / balance).replace([np.inf, -np.inf], np.nan).dropna()
    core_fraction = float(ratio.min())

    monthly_decay_core = _fit_plateau_decay(rolling_min.to_numpy())
    decay_annual_core = 1 - (1 - monthly_decay_core) ** 12

    residual = (balance - rolling_min).clip(lower=0)
    monthly_decay_noncore = _fit_pooled_decay(residual.to_numpy(), reset_ratio=0.05)
    decay_annual_noncore = 1 - (1 - monthly_decay_noncore) ** 12

    avg_life_years = (
        core_fraction * (1.0 / decay_annual_core if decay_annual_core > 0 else 30.0)
        + (1 - core_fraction) * (1.0 / decay_annual_noncore if decay_annual_noncore > 0 else 30.0)
    )

    return {
        "core_fraction": core_fraction,
        "decay_annual_core": decay_annual_core,
        "decay_annual_noncore": decay_annual_noncore,
        "avg_life_years": avg_life_years,
    }
