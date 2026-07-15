"""
Rate indices a position can reprice off.

  SHORT   - curve spot at ~1 month.
  TENOR   - curve spot at the position's origination_tenor_years.
  FIXED   - alias for TENOR (separate label in balance_sheet.yaml).
  ADMIN   - administered rate; handled by the engine's lag/beta mechanic,
            not by index_rate().
  MCLR    - see compute_mclr() below; handled by the engine, not by
            index_rate().
"""

import config


def index_rate(index: str, curve, tenor_years: float = None) -> float:
    if index in ("TENOR", "FIXED"):
        if tenor_years is None:
            raise ValueError(f"{index} index requires tenor_years")
        return curve.spot(tenor_years)
    if index == "SHORT":
        return curve.spot(1 / 12)
    if index == "TBILL3M":
        return curve.spot(0.25) + config.TBILL_OIS_BASIS_SPREAD
    raise ValueError(f"index_rate does not resolve index {index!r} directly (ADMIN/MCLR are engine-driven)")


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
