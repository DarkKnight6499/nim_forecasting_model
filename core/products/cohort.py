"""Shared cohort state for variable and fixed_amortizing positions."""

from dataclasses import dataclass


@dataclass
class Cohort:
    balance: float
    rate: float
    day0_rate: float
    day0_index_value: float
    origination_month: int
    phase: int = 0


def aggregate(cohorts):
    balance = sum(c.balance for c in cohorts)
    rate = (sum(c.balance * c.rate for c in cohorts) / balance) if balance else 0.0
    return balance, rate
