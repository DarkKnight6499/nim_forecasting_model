"""
Stylized historical curve-shift paths, for FTP policy spread calibration
(core/ftp_calibration.py). Illustrative shapes only, not fitted to actual
historical data: each defines a short-end and long-end (10y) bps shift per
month, ramped to full strength over the stated number of months and then
held, applied via curve.shocks._linear_in_tenor (the same short/long linear
shape as shocks.steepener/flattener).
"""

from curve.shocks import _linear_in_tenor
from curve.scenarios import CurvePath


def _path_from_monthly_bps(base_curve, monthly_short_long_bps):
    curves = []
    for short_bps, long_bps in monthly_short_long_bps:
        shift_fn = _linear_in_tenor(short_bps, long_bps, from_years=0.0, to_years=10.0)
        curves.append(base_curve.shifted(shift_fn))
    return CurvePath(curves)


def collapse_2008(base_curve, horizon_months=24):
    """Aggressive Fed cuts: short end falls ~500bps over 12 months then holds; long end falls ~150bps (curve steepens)."""
    monthly = [(-500 * min(1.0, (m + 1) / 12), -150 * min(1.0, (m + 1) / 12)) for m in range(horizon_months)]
    return _path_from_monthly_bps(base_curve, monthly)


def taper_steepening_2013(base_curve, horizon_months=24):
    """Short end flat; long end rises ~150bps over 12 months (curve steepens on tightening expectations)."""
    monthly = [(0.0, 150 * min(1.0, (m + 1) / 12)) for m in range(horizon_months)]
    return _path_from_monthly_bps(base_curve, monthly)


def hiking_2018(base_curve, horizon_months=24):
    """Gradual, roughly parallel rise of 200bps over the full horizon."""
    monthly = [(200 * (m + 1) / horizon_months, 200 * (m + 1) / horizon_months) for m in range(horizon_months)]
    return _path_from_monthly_bps(base_curve, monthly)


def crash_2020(base_curve, horizon_months=24):
    """Short end collapses ~425bps within 3 months; long end falls ~150bps (curve steepens)."""
    monthly = [(-425 * min(1.0, (m + 1) / 3), -150 * min(1.0, (m + 1) / 3)) for m in range(horizon_months)]
    return _path_from_monthly_bps(base_curve, monthly)


HISTORICAL_CYCLES = {
    "2008 collapse": collapse_2008,
    "2013 taper steepening": taper_steepening_2013,
    "2018 hiking": hiking_2018,
    "2020 crash to zero": crash_2020,
}
