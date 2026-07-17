"""
Builds a monthly sequence of YieldCurve objects for each rate scenario: the
shock ramps in linearly over `ramp_months`, then holds at full strength for
the rest of the horizon. A scenario can also carry basis_shocks (per-index
overlay shifts, see curve/basis.py and core/indices.py), ramped the same
way; scenarios without any are unaffected (basis_overlays stays None).
"""

from dataclasses import dataclass

import config
from curve.yield_curve import YieldCurve


@dataclass
class ScenarioDef:
    """A rate scenario: a curve shift plus optional per-index basis shocks."""
    shift_fn: object  # tenor_years -> decimal shift, e.g. curve/shocks.py builders
    basis_shocks: dict = None  # {index_name: bps_shift}, ramped like the curve shock


def as_scenario_def(value):
    """Accepts a plain shift_fn, a (shift_fn, basis_shocks) tuple (so config.py
    doesn't need to import this module to build scenario defs), or a ScenarioDef."""
    if isinstance(value, ScenarioDef):
        return value
    if isinstance(value, tuple):
        shift_fn, basis_shocks = value
        return ScenarioDef(shift_fn=shift_fn, basis_shocks=basis_shocks)
    return ScenarioDef(shift_fn=value)


class CurvePath:
    """One scenario: a YieldCurve for each month over the forecast horizon."""

    def __init__(self, curves, label="scenario", basis_overlays=None):
        self.curves = curves
        self.label = label
        self.basis_overlays = basis_overlays  # optional: list of {index: BasisOverlay}, one per month

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

    def index_basis(self, month: int, index: str):
        """Returns the shocked BasisOverlay for `index` at `month` if this scenario
        carries a basis shock for it, else the static default from
        config.INDEX_BASIS (or None if that index has no basis at all)."""
        if self.basis_overlays is not None and index in self.basis_overlays[month]:
            return self.basis_overlays[month][index]
        return config.INDEX_BASIS.get(index)


def build_curve_path(base_curve: YieldCurve, scenario_def, horizon_months: int, ramp_months: int) -> CurvePath:
    scenario_def = as_scenario_def(scenario_def)
    shift_fn = scenario_def.shift_fn

    curves = []
    basis_overlays = [] if scenario_def.basis_shocks else None
    for m in range(horizon_months):
        ramp_fraction = min(1.0, (m + 1) / ramp_months) if ramp_months > 0 else 1.0
        ramped_shift = (lambda t, _f=shift_fn, _r=ramp_fraction: _f(t) * _r)
        curves.append(base_curve.shifted(ramped_shift))
        if scenario_def.basis_shocks:
            month_overlays = {}
            for index, bps in scenario_def.basis_shocks.items():
                base_overlay = config.INDEX_BASIS.get(index) or config.ZERO_BASIS_OVERLAY
                month_overlays[index] = base_overlay.shifted(bps / 10000 * ramp_fraction)
            basis_overlays.append(month_overlays)
    return CurvePath(curves, basis_overlays=basis_overlays)


def build_curve_scenarios(base_curve: YieldCurve, scenario_defs: dict, horizon_months: int, ramp_months: int) -> dict:
    """scenario_defs: {label: shift_fn} or {label: ScenarioDef}. Returns {label: CurvePath}."""
    paths = {}
    for label, scenario_def in scenario_defs.items():
        path = build_curve_path(base_curve, scenario_def, horizon_months, ramp_months)
        path.label = label
        paths[label] = path
    return paths
