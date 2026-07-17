"""
Pooled replicating-portfolio FTP, for non-maturity deposits (administered
positions). Models the position's transfer rate as a rolling ladder of
behavioral_duration_years-equivalent tranches (core.position._ladder_months_for_duration):
each month 1/n of the ladder rolls onto a new tranche priced at the curve +
spread for the full ladder tenor, blended with the (n-1)/n still carrying
their prior locked-in rate. Unlike matched_maturity's single fixed-tenor
lookup, old tranches don't reprice the instant the curve moves - only the
maturing slice does, which is the point of using a replicating portfolio for
a position with no real contractual maturity.
"""

from core.ftp.spread_curve import spread_for_tenor


def build_rate_series(position, curve_path, benchmark_rate_for_tenors):
    n = position._ladder_months_for_duration() if position.behavioral_duration_years else 12
    tenor_years = n / 12
    spread = spread_for_tenor(tenor_years)

    rates = []
    for t, curve in enumerate(curve_path.curves):
        renewal_rate = curve.spot(tenor_years) + spread
        if t == 0:
            rates.append(renewal_rate)
        else:
            rates.append(((n - 1) * rates[-1] + renewal_rate) / n)
    return rates
