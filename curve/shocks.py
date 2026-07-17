"""
Curve shock builders: each returns a shift_fn(tenor_years) -> decimal shift,
consumed by YieldCurve.shifted().
"""


def parallel(bps: float):
    """Same shift at every tenor."""
    shift = bps / 10000
    return lambda tenor_years: shift


def steepener(short_bps: float, long_bps: float, pivot_years: float = 2.0):
    """Short end moves by short_bps, long end (10y) by long_bps, linear in between."""
    return _linear_in_tenor(short_bps, long_bps, from_years=0.0, to_years=10.0)


def flattener(short_bps: float, long_bps: float, pivot_years: float = 2.0):
    """Same shape as steepener; label reflects intended usage (short up / long down, etc)."""
    return _linear_in_tenor(short_bps, long_bps, from_years=0.0, to_years=10.0)


def twist(pivot_years: float, short_bps: float, long_bps: float):
    """Shift is short_bps at tenor 0, 0 at pivot_years, long_bps at 10y (linear each side)."""
    short_shift = short_bps / 10000
    long_shift = long_bps / 10000

    def shift_fn(tenor_years):
        if tenor_years <= pivot_years:
            frac = tenor_years / pivot_years if pivot_years > 0 else 1.0
            return short_shift * (1 - frac)
        frac = (tenor_years - pivot_years) / (10.0 - pivot_years) if pivot_years < 10.0 else 1.0
        return long_shift * min(1.0, frac)

    return shift_fn


def short_up(bps: float = 100, fade_years: float = 2.0):
    """Short end moves by bps, fading linearly to 0 by fade_years (one of the standard six IRRBB scenarios)."""
    return _linear_in_tenor(bps, 0.0, from_years=0.0, to_years=fade_years)


def short_down(bps: float = 100, fade_years: float = 2.0):
    """Short end moves by -bps, fading linearly to 0 by fade_years (one of the standard six IRRBB scenarios)."""
    return _linear_in_tenor(-bps, 0.0, from_years=0.0, to_years=fade_years)


def custom(shifts_by_tenor: dict):
    """Interpolated shift between explicit (tenor_years -> bps) points."""
    import numpy as np
    tenors = sorted(shifts_by_tenor.keys())
    shifts = [shifts_by_tenor[t] / 10000 for t in tenors]
    return lambda tenor_years: float(np.interp(tenor_years, tenors, shifts))


def _linear_in_tenor(short_bps, long_bps, from_years, to_years):
    short_shift = short_bps / 10000
    long_shift = long_bps / 10000

    def shift_fn(tenor_years):
        frac = (tenor_years - from_years) / (to_years - from_years)
        frac = min(1.0, max(0.0, frac))
        return short_shift + (long_shift - short_shift) * frac

    return shift_fn
