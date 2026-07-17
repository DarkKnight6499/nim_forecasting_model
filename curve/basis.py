"""
Per-index basis overlay: a spread curve (by tenor) added on top of the base
yield curve when projecting an index's rate. Discounting (EVE, MTM, FTP
tranche PVs) always stays on the base curve plus the position's own
z-spread (core/cashflows.py) - this module only affects index PROJECTION,
not discounting. Two-curve convention: projection = base curve + index
basis; discounting = base curve + position z-spread. Before this module,
every index (SHORT, TENOR, FIXED, TBILL3M) read the base curve directly
(TBILL3M's spread was a single hardcoded constant), so there was no way to
represent one index moving differently from the base curve or from another
index (e.g. a funding-cost index widening while a lending index stays put).
"""

from dataclasses import dataclass

import numpy as np


@dataclass
class BasisOverlay:
    spreads_by_tenor: dict  # tenor_years -> decimal spread, added to the base curve's zero rate

    def spread(self, tenor_years: float) -> float:
        """Spread at `tenor_years`, linearly interpolated; flat beyond the ends."""
        tenors = sorted(self.spreads_by_tenor.keys())
        spreads = [self.spreads_by_tenor[t] for t in tenors]
        return float(np.interp(tenor_years, tenors, spreads))

    def shifted(self, shift_decimal: float) -> "BasisOverlay":
        """Returns a new overlay with shift_decimal added at every tenor point."""
        return BasisOverlay({t: s + shift_decimal for t, s in self.spreads_by_tenor.items()})
