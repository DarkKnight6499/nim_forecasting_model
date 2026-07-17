"""
Synthetic monthly balance history for a CASA-style deposit product, so
core/nmd_estimation.py is demonstrable without a real deposit history CSV.

Core is a slow, steady decline plus small noise (the sticky floor). Non-core
is modeled as a sequence of replenishment events (new hot money arriving at
irregular intervals) each decaying at decay_annual_noncore until the next
one arrives - not a single smooth trend, since real volatile deposits get
topped up periodically rather than draining away for good.
"""

import numpy as np
import pandas as pd


def generate_balance_history(core_fraction=0.65, decay_annual_core=0.15, decay_annual_noncore=0.80,
                              starting_balance=500_000_000, months=36, seed=42, product="Synthetic CASA"):
    rng = np.random.default_rng(seed)

    monthly_core = 1 - (1 - decay_annual_core) ** (1 / 12)
    monthly_noncore = 1 - (1 - decay_annual_noncore) ** (1 / 12)

    core0 = starting_balance * core_fraction
    noncore0 = starting_balance * (1 - core_fraction)

    t = np.arange(months)
    core_noise = 1 + rng.normal(0, 0.005, size=months)
    core_series = core0 * (1 - monthly_core) ** t * core_noise

    noncore_series = np.zeros(months)
    next_replenishment = 0
    while next_replenishment < months:
        spacing = rng.integers(10, 18)
        replenishment_size = noncore0 * rng.uniform(0.85, 1.15)
        end = min(months, next_replenishment + spacing)
        age = np.arange(end - next_replenishment)
        noncore_series[next_replenishment:end] = replenishment_size * (1 - monthly_noncore) ** age
        next_replenishment = end
    noncore_noise = 1 + rng.normal(0, 0.02, size=months)
    noncore_series = np.maximum(0.0, noncore_series * noncore_noise)

    total = core_series + noncore_series
    return pd.DataFrame({"month": t, "product": product, "balance": total})
