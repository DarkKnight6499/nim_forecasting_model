"""FTP liquidity-premium spread: config.FTP_CURVE_SPREADS_BY_TENOR_YEARS, interpolated by tenor and floored for short tenors."""

import numpy as np

import config


def spread_for_tenor(tenor_years, spreads_by_tenor=None):
    """spreads_by_tenor overrides config.FTP_CURVE_SPREADS_BY_TENOR_YEARS (core/ftp_calibration.py uses this)."""
    curve = spreads_by_tenor if spreads_by_tenor is not None else config.FTP_CURVE_SPREADS_BY_TENOR_YEARS
    tenors = sorted(curve.keys())
    spreads = [curve[t] for t in tenors]
    spread = float(np.interp(tenor_years, tenors, spreads))
    if tenor_years < config.FTP_SHORT_TENOR_CUTOFF_YEARS:
        spread = max(spread, config.FTP_SHORT_TENOR_MIN_SPREAD)
    return spread
