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

Tracked as an actual deque of n tranche rates (equal-weighted), one slot per
ladder month. At t=0 there is no origination history, so every tranche is
back-filled flat at the month-0 renewal rate (documented approximation, same
convention used elsewhere for t=0 back-fill). Each subsequent month the
oldest tranche rolls off and is replaced by that month's renewal rate, so a
tranche's influence on the blended rate is exactly zero once n months have
passed, not merely decayed - a true ladder forgets, it does not fade.
"""

from collections import deque

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

    renewal_rate_0 = curve_path.curves[0].spot(tenor_years) + spread
    tranches = deque([renewal_rate_0] * n, maxlen=n)
    tranche_total = renewal_rate_0 * n

    rates = [tranche_total / n]
    for t in range(1, len(curve_path.curves)):
        renewal_rate = curve_path.curves[t].spot(tenor_years) + spread
        oldest = tranches[0]
        tranches.append(renewal_rate)
        tranche_total += renewal_rate - oldest
        rates.append(tranche_total / n)
    return rates
