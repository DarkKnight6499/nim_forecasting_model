"""Straight-spread FTP: overnight curve rate plus a single flat policy spread, no tenor/duration lookup."""

import config


def build_rate_series(position, curve_path, benchmark_rate_for_tenors, cohort_detail_df=None, spreads_by_tenor=None):
    return [curve.spot(1 / 12) + config.FTP_STRAIGHT_SPREAD for curve in curve_path.curves]
