"""
Builds a monthly sequence of YieldCurve objects for each rate scenario: the
shock (a curve shift function) ramps in linearly over `ramp_months` (mirrors
how the Fed/market typically moves - gradually, not in one jump), then holds
at full strength for the rest of the horizon. Same ramp convention as the
pre-Phase-0 scalar model, just applied as a curve transform instead of one
overnight number.
"""

from curve.yield_curve import YieldCurve


class CurvePath:
    """One scenario: a YieldCurve for each month over the forecast horizon."""

    def __init__(self, curves, label="scenario"):
        self.curves = curves
        self.label = label

    def __len__(self):
        return len(self.curves)

    def spot(self, month: int, tenor_years: float) -> float:
        return self.curves[month].spot(tenor_years)

    def short_rate(self, month: int) -> float:
        """Convenience: spot at the shortest standard tenor (the old 'benchmark rate')."""
        return self.curves[month].spot(self.curves[month].tenors[0])

    def short_rate_array(self):
        """Full monthly short-rate series, for feeding legacy scalar-path consumers."""
        import numpy as np
        return np.array([self.short_rate(m) for m in range(len(self.curves))])


def build_curve_path(base_curve: YieldCurve, shift_fn, horizon_months: int, ramp_months: int) -> CurvePath:
    curves = []
    for m in range(horizon_months):
        ramp_fraction = min(1.0, (m + 1) / ramp_months) if ramp_months > 0 else 1.0
        ramped_shift = (lambda t, _f=shift_fn, _r=ramp_fraction: _f(t) * _r)
        curves.append(base_curve.shifted(ramped_shift))
    return CurvePath(curves)


def build_curve_scenarios(base_curve: YieldCurve, scenario_defs: dict, horizon_months: int, ramp_months: int) -> dict:
    """scenario_defs: {label: shift_fn(tenor_years) -> decimal}. Returns {label: CurvePath}."""
    paths = {}
    for label, shift_fn in scenario_defs.items():
        path = build_curve_path(base_curve, shift_fn, horizon_months, ramp_months)
        path.label = label
        paths[label] = path
    return paths
