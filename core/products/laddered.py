"""Laddered mechanics: single blended state, 1/ladder_months matures/renews each month at the current curve tenor."""

from core.indices import index_rate


def step(p, prev_balance, prev_rate, curve_t, t):
    maturing = prev_balance / p.ladder_months
    growth = prev_balance * (p.growth_rate_annual / 12)
    renewed = maturing + max(0.0, growth)
    new_rate_piece = index_rate(p.index, curve_t, tenor_years=p.origination_tenor_years) + p.spread
    if p.rate_floor is not None:
        new_rate_piece = max(new_rate_piece, p.rate_floor)
    surviving = prev_balance - maturing
    new_balance = surviving + renewed
    if new_balance <= 0:
        return 0.0, new_rate_piece, renewed, new_rate_piece
    blended_rate = (surviving * prev_rate + renewed * new_rate_piece) / new_balance
    return new_balance, blended_rate, renewed, new_rate_piece
