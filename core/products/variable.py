"""
Variable-rate mechanics: reset_frequency_months cohorts, one per reset-phase
slot. On its due month, a cohort's rate is set from its index:
  MCLR: mclr(month) + spread.
  SHORT/TENOR/TBILL3M: day0_rate + beta * (index_now - index_at_cohort_creation).
Growth is split evenly across phase-cohorts.
"""

import config
from core.indices import index_rate
from core.products.cohort import Cohort


def seed(p, curve0):
    n = max(1, p.reset_frequency_months)
    piece = p.balance / n
    idx0 = 0.0 if p.index == "MCLR" else index_rate(p.index, curve0, tenor_years=p.origination_tenor_years)
    return [
        Cohort(balance=piece, rate=p.rate, day0_rate=p.rate, day0_index_value=idx0,
               origination_month=0, phase=phase)
        for phase in range(n)
    ]


def step(p, cohorts, curve_t, t, mclr_t):
    growth_m = p.growth_rate_annual / 12
    if p.seasonal:
        growth_m *= config.SEASONALITY_INDEX_DEPOSITS[t % 12]
    total_growth = sum(c.balance for c in cohorts) * growth_m
    per_cohort_growth = total_growth / len(cohorts)

    for c in cohorts:
        c.balance += per_cohort_growth
        if t % p.reset_frequency_months == c.phase:
            if p.index == "MCLR":
                new_rate = mclr_t + p.spread
            else:
                idx_now = index_rate(p.index, curve_t, tenor_years=p.origination_tenor_years)
                new_rate = c.day0_rate + p.beta * (idx_now - c.day0_index_value)
            if p.rate_floor is not None:
                new_rate = max(new_rate, p.rate_floor)
            c.rate = max(new_rate, 0.0)
