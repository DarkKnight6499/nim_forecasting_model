"""
Contract every FTP method module implements:

    build_rate_series(position, curve_path, benchmark_rate_for_tenors) -> list[float]

Returns one FTP rate per month in curve_path (length == len(curve_path.curves)).
benchmark_rate_for_tenors is only used by methods that size a fixed tenor once
up front (matched_maturity, pooled_replicating) - kept fixed across scenarios
so a position's transfer-pricing tenor doesn't itself move with the scenario.
"""
