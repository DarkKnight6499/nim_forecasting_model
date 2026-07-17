"""
Administered (NMD) mechanics: single balance/rate state, beta/lag repricing
off the short end. If pricing_elasticity is set, growth also responds to how
competitive this position's own (just-computed) rate is against today's
market rate (core/elasticity.py) - a position whose beta lags the market in
a rising cycle sees growth slow or reverse instead of continuing obliviously.
"""

import config
from core.elasticity import volume_response


def step(p, prev_balance, prev_rate, curve0, curve_lag, curve_t, t):
    delta = curve_lag.spot(1 / 12) - curve0.spot(1 / 12)
    new_rate = p.rate + p.beta * delta
    if p.rate_floor is not None:
        new_rate = max(new_rate, p.rate_floor)
    new_rate = max(new_rate, 0.0)
    growth_m = p.growth_rate_annual / 12
    if p.seasonal:
        growth_m *= config.SEASONALITY_INDEX_DEPOSITS[t % 12]
    if p.pricing_elasticity:
        growth_m *= volume_response(new_rate, curve_t.spot(1 / 12), p.pricing_elasticity)
    new_balance = prev_balance * (1 + growth_m)
    return new_balance, new_rate
