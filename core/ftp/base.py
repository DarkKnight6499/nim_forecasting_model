"""
Contract every FTP method module implements:

    build_rate_series(position, curve_path, benchmark_rate_for_tenors, cohort_detail_df=None) -> list[float]

Returns one FTP rate per month in curve_path (length == len(curve_path.curves)).
benchmark_rate_for_tenors is only used by methods that size a fixed tenor once
up front (matched_maturity's non-cohort fallback, pooled_replicating's
administered case) - kept fixed across scenarios so a position's
transfer-pricing tenor doesn't itself move with the scenario.
cohort_detail_df (core/engine.py's per-vintage fixed_amortizing balances) is
only used by matched_maturity's fixed_amortizing path; every other method
ignores it.
"""
