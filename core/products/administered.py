"""Administered (NMD) mechanics: single balance/rate state, beta/lag repricing off the short end."""

import config


def step(p, prev_balance, prev_rate, curve0, curve_lag, t):
    delta = curve_lag.spot(1 / 12) - curve0.spot(1 / 12)
    new_rate = p.rate + p.beta * delta
    if p.rate_floor is not None:
        new_rate = max(new_rate, p.rate_floor)
    new_rate = max(new_rate, 0.0)
    growth_m = p.growth_rate_annual / 12
    if p.seasonal:
        growth_m *= config.SEASONALITY_INDEX_DEPOSITS[t % 12]
    new_balance = prev_balance * (1 + growth_m)
    return new_balance, new_rate
