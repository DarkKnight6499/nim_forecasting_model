"""Matched-maturity FTP: curve rate at the position's own effective-duration tenor, plus the FTP liquidity spread at that tenor."""

from core.position import bucket_effective_duration
from core.ftp.spread_curve import spread_for_tenor


def build_rate_series(position, curve_path, benchmark_rate_for_tenors):
    tenor_years = bucket_effective_duration(position, benchmark_rate_for_tenors)
    spread = spread_for_tenor(tenor_years)
    return [curve.spot(tenor_years) + spread for curve in curve_path.curves]
