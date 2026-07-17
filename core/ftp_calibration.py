"""
FTP policy spread calibration: tunes config.FTP_CURVE_SPREADS_BY_TENOR_YEARS
to minimize the variance of monthly ALM desk P&L across a library of
historical rate-cycle curve paths (curve/historical_cycles.py), subject to
the short-tenor minimum spread floor (config.FTP_SHORT_TENOR_MIN_SPREAD).

A well-calibrated FTP policy spread curve keeps ALM desk P&L (the residual
the treasury desk is actually exposed to once customer margin is immunized)
as flat as possible across plausible rate cycles; high variance there means
the spread curve is mispriced relative to the book's real repricing/prepayment
behavior.
"""

import copy

import numpy as np
from scipy.optimize import minimize

import config
from core.engine import run_scenario
from core.ftp.aggregate import compute_ftp_pnl

_TENORS = sorted(config.FTP_CURVE_SPREADS_BY_TENOR_YEARS.keys())


def _alm_pnl_series(positions, curve_path, benchmark_rate, spreads_by_tenor):
    _, detail_df, cohort_detail_df = run_scenario(copy.deepcopy(positions), curve_path, scenario_label="calib")
    _, monthly = compute_ftp_pnl(
        positions, detail_df, curve_path, benchmark_rate,
        cohort_detail_df=cohort_detail_df, spreads_by_tenor=spreads_by_tenor,
    )
    return monthly["alm_desk_pnl"].to_numpy()


def cross_cycle_variance(positions, cycle_paths, benchmark_rate, spreads_by_tenor=None):
    """Sum of each cycle's monthly ALM desk P&L variance, using spreads_by_tenor (or config's current table)."""
    total = 0.0
    for curve_path in cycle_paths.values():
        pnl = _alm_pnl_series(positions, curve_path, benchmark_rate, spreads_by_tenor)
        total += float(np.var(pnl))
    return total


def calibrate_policy_spreads(positions, cycle_paths, benchmark_rate):
    """Returns a calibrated {tenor_years: spread} table minimizing cross_cycle_variance."""
    bounds = [
        (config.FTP_SHORT_TENOR_MIN_SPREAD, 0.02) if t < config.FTP_SHORT_TENOR_CUTOFF_YEARS else (0.0, 0.02)
        for t in _TENORS
    ]
    x0 = np.array([
        np.clip(config.FTP_CURVE_SPREADS_BY_TENOR_YEARS[t], lo, hi)
        for t, (lo, hi) in zip(_TENORS, bounds)
    ])

    def objective(x):
        spreads_by_tenor = dict(zip(_TENORS, x))
        return cross_cycle_variance(positions, cycle_paths, benchmark_rate, spreads_by_tenor=spreads_by_tenor)

    result = minimize(objective, x0, method="Nelder-Mead", bounds=bounds,
                       options={"xatol": 1e-5, "fatol": 1e-2, "maxiter": 300})
    return dict(zip(_TENORS, result.x))
