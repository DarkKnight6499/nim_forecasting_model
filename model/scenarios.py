"""
Builds monthly benchmark-rate paths for each rate scenario: a starting rate,
shocked by some number of bps, ramped in linearly over `ramp_months` (this
mirrors how the Fed/market typically moves - gradually, not in one jump) and
then held flat for the rest of the horizon.
"""

import numpy as np


def build_benchmark_path(starting_rate, shock_bps, horizon_months, ramp_months):
    shock = shock_bps  # decimal, e.g. 0.01 for +100bps
    path = np.empty(horizon_months)
    for m in range(horizon_months):
        ramp_fraction = min(1.0, (m + 1) / ramp_months) if ramp_months > 0 else 1.0
        path[m] = starting_rate + shock * ramp_fraction
    return np.clip(path, 0.0, None)  # rates floored at 0


def build_all_scenarios(starting_rate, scenarios: dict, horizon_months, ramp_months):
    """scenarios: {label: shock_bps_decimal}. Returns {label: np.array of length horizon_months}."""
    return {
        label: build_benchmark_path(starting_rate, shock, horizon_months, ramp_months)
        for label, shock in scenarios.items()
    }
