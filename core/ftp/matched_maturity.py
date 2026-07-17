"""
Matched-maturity FTP: origination-locked transfer rates, not floating.

fixed_amortizing: each vintage cohort's FTP rate is set once, from the curve
prevailing in its origination month at the position's origination_tenor_years,
and never moves again - this is what immunizes a fixed-rate loan's customer
margin from the scenario's rate path. Needs cohort_detail_df (core/engine.py)
for the vintage balance mix, since position_detail_df only carries a
position-aggregated balance/rate per month.

variable: the FTP rate is set at the position's reset tenor
(reset_frequency_months, in years) and re-fixed only on reset months,
mirroring the position's own contractual reset cadence. Since every reset is
on a fixed schedule, the month a cohort's rate was last fixed is a closed
form of t and reset_frequency_months - no per-cohort state needed.

Anything else (a position explicitly set to matched_maturity that is neither
variable nor fixed_amortizing) falls back to a single fixed-duration curve
point recomputed every month - the un-locked approximation the two paths
above exist to avoid, kept only so the method never raises.
"""

from core.position import bucket_effective_duration
from core.ftp.spread_curve import spread_for_tenor


def _variable_rate_series(position, curve_path, spreads_by_tenor):
    n = max(1, position.reset_frequency_months)
    tenor_years = n / 12
    spread = spread_for_tenor(tenor_years, spreads_by_tenor)
    rates = []
    for t in range(len(curve_path.curves)):
        last_reset_month = t - (t % n)
        rates.append(curve_path.curves[last_reset_month].spot(tenor_years) + spread)
    return rates


def _fixed_amortizing_rate_series(position, curve_path, cohort_detail_df, spreads_by_tenor):
    tenor_years = position.origination_tenor_years
    spread = spread_for_tenor(tenor_years, spreads_by_tenor)
    rows = cohort_detail_df[cohort_detail_df["bucket"] == position.name]

    rates = []
    for t in range(len(curve_path.curves)):
        month_rows = rows[rows["month"] == t]
        total_balance = month_rows["balance"].sum()
        if total_balance <= 0:
            rates.append(curve_path.curves[0].spot(tenor_years) + spread)
            continue
        weighted_rate = sum(
            row.balance * (curve_path.curves[int(row.origination_month)].spot(tenor_years) + spread)
            for row in month_rows.itertuples()
        )
        rates.append(weighted_rate / total_balance)
    return rates


def build_rate_series(position, curve_path, benchmark_rate_for_tenors, cohort_detail_df=None, spreads_by_tenor=None):
    if position.category_type == "variable":
        return _variable_rate_series(position, curve_path, spreads_by_tenor)
    if position.category_type == "fixed_amortizing" and cohort_detail_df is not None and len(cohort_detail_df):
        return _fixed_amortizing_rate_series(position, curve_path, cohort_detail_df, spreads_by_tenor)

    tenor_years = bucket_effective_duration(position, benchmark_rate_for_tenors)
    spread = spread_for_tenor(tenor_years, spreads_by_tenor)
    return [curve.spot(tenor_years) + spread for curve in curve_path.curves]
