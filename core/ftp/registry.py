"""Maps a position's ftp_method name to its rate-series builder (core/ftp/base.py contract)."""

from core.ftp import matched_maturity, pooled_replicating, straight_spread

BUILDERS = {
    "matched_maturity": matched_maturity.build_rate_series,
    "pooled_replicating": pooled_replicating.build_rate_series,
    "straight_spread": straight_spread.build_rate_series,
}


def build_rate_series(position, curve_path, benchmark_rate_for_tenors, cohort_detail_df=None, spreads_by_tenor=None):
    builder = BUILDERS[position.ftp_method]
    return builder(position, curve_path, benchmark_rate_for_tenors,
                    cohort_detail_df=cohort_detail_df, spreads_by_tenor=spreads_by_tenor)
