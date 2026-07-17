"""
Fixed-rate amortizing mechanics: vintage cohorts, each locked at its
origination coupon. Runs off at
  min(cpr_max, cpr_annual + refi_sensitivity * max(0, cohort.rate - new_production_rate))
Runoff + growth forms a new cohort priced at index_rate(index, tenor_years) + spread.
"""

from core.indices import index_rate
from core.products.cohort import Cohort


def _monthly_from_annual_rate(annual_rate):
    return 1 - (1 - annual_rate) ** (1 / 12) if annual_rate < 1 else annual_rate / 12


def seed(p, curve0):
    return [Cohort(balance=p.balance, rate=p.rate, day0_rate=p.rate, day0_index_value=0.0,
                    origination_month=0, phase=0)]


def step(p, cohorts, curve_t, t):
    new_prod_rate = index_rate(p.index, curve_t, tenor_years=p.origination_tenor_years) + p.spread
    if p.rate_floor is not None:
        new_prod_rate = max(new_prod_rate, p.rate_floor)

    total_runoff = 0.0
    for c in cohorts:
        cpr_annual_eff = min(p.cpr_max, p.cpr_annual + p.refi_sensitivity * max(0.0, c.rate - new_prod_rate))
        cpr_m = _monthly_from_annual_rate(cpr_annual_eff)
        runoff = c.balance * cpr_m
        c.balance -= runoff
        total_runoff += runoff

    total_balance = sum(c.balance for c in cohorts)
    growth = total_balance * (p.growth_rate_annual / 12)
    new_production = total_runoff + max(0.0, growth)
    if new_production > 0:
        cohorts.append(Cohort(balance=new_production, rate=new_prod_rate, day0_rate=new_prod_rate,
                               day0_index_value=0.0, origination_month=t, phase=0))
    cohorts[:] = [c for c in cohorts if c.balance > 1.0]
