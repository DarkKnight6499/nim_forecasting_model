"""
Rate indices a position can reprice off.

  SHORT   - curve spot at ~1 month.
  TENOR   - curve spot at the position's origination_tenor_years.
  FIXED   - alias for TENOR (separate label in balance_sheet.yaml).
  ADMIN   - administered rate; handled by the engine's lag/beta mechanic,
            not by index_rate().
  MCLR    - see compute_mclr() below; handled by the engine, not by
            index_rate().

Each of SHORT/TENOR/FIXED/TBILL3M also carries a basis overlay (added to
the curve's spot rate at that index's tenor) - see config.INDEX_BASIS and
curve/basis.py. basis_overlay, if passed, is the scenario's (possibly
shocked) overlay for this index and month; if None, falls back to
config.INDEX_BASIS's static default for the index.
"""

import config


def index_rate(index: str, curve, tenor_years: float = None, basis_overlay=None) -> float:
    if index in ("TENOR", "FIXED"):
        if tenor_years is None:
            raise ValueError(f"{index} index requires tenor_years")
        rate = curve.spot(tenor_years)
    elif index == "SHORT":
        tenor_years = 1 / 12
        rate = curve.spot(tenor_years)
    elif index == "TBILL3M":
        tenor_years = 0.25
        rate = curve.spot(tenor_years)
    else:
        raise ValueError(f"index_rate does not resolve index {index!r} directly (ADMIN/MCLR are engine-driven)")

    overlay = basis_overlay if basis_overlay is not None else config.INDEX_BASIS.get(index)
    if overlay is not None:
        rate += overlay.spread(tenor_years)
    return rate


def compute_mclr(new_deposit_weighted_rate: float, current_borrowing_rate: float, short_rate: float) -> float:
    """
    MCLR = MCLR_DEPOSIT_WEIGHT * new_deposit_weighted_rate
         + (1 - MCLR_DEPOSIT_WEIGHT) * current_borrowing_rate
         + MCLR_EQUITY_SPREAD.
    Falls back to short_rate + MCLR_EQUITY_SPREAD if either input is None.
    """
    if new_deposit_weighted_rate is None or current_borrowing_rate is None:
        return short_rate + config.MCLR_EQUITY_SPREAD
    return (
        config.MCLR_DEPOSIT_WEIGHT * new_deposit_weighted_rate
        + (1 - config.MCLR_DEPOSIT_WEIGHT) * current_borrowing_rate
        + config.MCLR_EQUITY_SPREAD
    )
