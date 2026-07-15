"""
A term structure of zero (spot) rates: interpolation, implied-forward
extraction, discount factors, and shifting.
"""

import numpy as np

STANDARD_TENORS = [1 / 12, 0.25, 0.5, 1.0, 2.0, 3.0, 5.0, 10.0]  # years


class YieldCurve:
    """Zero-rate curve. Rates are annualized decimals (e.g. 0.0425 = 4.25%)."""

    def __init__(self, tenors, zero_rates, as_of_month: int = 0):
        if len(tenors) != len(zero_rates):
            raise ValueError("tenors and zero_rates must be the same length")
        order = np.argsort(tenors)
        self.tenors = np.asarray(tenors, dtype=float)[order]
        self.zero_rates = np.asarray(zero_rates, dtype=float)[order]
        self.as_of_month = as_of_month

    def spot(self, tenor_years: float) -> float:
        """Zero rate at `tenor_years`, linearly interpolated; flat beyond the ends."""
        return float(np.interp(tenor_years, self.tenors, self.zero_rates))

    def forward(self, start_years: float, end_years: float) -> float:
        """Implied annualized forward rate between two tenor points on this curve."""
        if end_years <= start_years:
            raise ValueError("end_years must be greater than start_years")
        z1, z2 = self.spot(start_years), self.spot(end_years)
        if start_years == 0:
            return z2
        growth = (1 + z2) ** end_years / (1 + z1) ** start_years
        return growth ** (1 / (end_years - start_years)) - 1

    def df(self, tenor_years: float) -> float:
        """Discount factor for `tenor_years` implied by this curve's spot rate."""
        if tenor_years <= 0:
            return 1.0
        return 1.0 / (1 + self.spot(tenor_years)) ** tenor_years

    def shifted(self, shift_fn) -> "YieldCurve":
        """Returns a new curve with shift_fn(tenor_years) -> decimal added at each tenor point."""
        new_rates = np.array([max(0.0, z + shift_fn(t)) for t, z in zip(self.tenors, self.zero_rates)])
        return YieldCurve(self.tenors.copy(), new_rates, self.as_of_month)
