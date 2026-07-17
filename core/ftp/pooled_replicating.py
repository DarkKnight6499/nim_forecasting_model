"""
Pooled replicating-portfolio FTP: models a position's transfer rate as a
rolling ladder of tranches, each locked at the curve + spread for the full
ladder tenor when it rolls. Each month 1/n of the ladder rolls onto a new
tranche; the other (n-1)/n keep carrying their prior locked-in rate. Unlike
matched_maturity's single fixed-tenor lookup, old tranches don't reprice the
instant the curve moves - only the maturing slice does.

For administered (NMD) positions, n is the behavioral-duration-equivalent
ladder length (core.position._ladder_months_for_duration) - there's no real
contractual maturity, so the ladder proxies one. For laddered positions
(investment securities, CDs, term borrowings), n is the position's own
ladder_months - the same rolling mechanic applies directly, since those
books are already a real maturing ladder.
"""

from core.ftp.spread_curve import spread_for_tenor


def _ladder_length_months(position):
    if position.category_type == "administered" and position.behavioral_duration_years:
        return position._ladder_months_for_duration()
    if position.category_type == "laddered":
        return position.ladder_months
    return 12


def build_rate_series(position, curve_path, benchmark_rate_for_tenors, cohort_detail_df=None, spreads_by_tenor=None):
    n = _ladder_length_months(position)
    tenor_years = n / 12
    spread = spread_for_tenor(tenor_years, spreads_by_tenor)

    rates = []
    for t, curve in enumerate(curve_path.curves):
        renewal_rate = curve.spot(tenor_years) + spread
        if t == 0:
            rates.append(renewal_rate)
        else:
            rates.append(((n - 1) * rates[-1] + renewal_rate) / n)
    return rates
