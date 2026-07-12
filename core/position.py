"""
Position: the cashflow-first balance sheet unit that replaces config.Bucket.

Every position exposes two schedule methods, used uniformly across the gap,
liquidity, duration/EVE, and FTP reports (previously each report had its own
half-copied bucketing logic):

  repricing_schedule(max_months)  - how much of the CURRENT balance has its
                                     RATE reset in each future month. Drives
                                     the interest rate sensitivity gap report.
  cashflow_schedule(max_months)   - how much of the CURRENT balance actually
                                     matures/pays down as CASH in each future
                                     month. Drives the structural liquidity
                                     statement.

These two are deliberately different for non-maturity deposits (administered
positions): a rate reset is not a cash outflow, since NOW/savings/MMDA have
no contractual maturity. `liquidity_decay_annual` (core-deposit attrition)
drives cashflow_schedule; the behavioral duration ladder drives
repricing_schedule. Every other category_type already has one real cashflow
timing (loan paydowns, security/CD/borrowing maturities) so cashflow_schedule
reuses repricing_schedule for those.

Fixes a real bug from the pre-Phase-0 model: the old repricing gap slotted
the ENTIRE administered balance as a point mass at `lag_months` (so a 3.5-year
duration NOW-Core book showed up as fully rate-sensitive within one quarter),
while the EVE report gave the same balance a multi-year behavioral duration -
two reports describing two different banks. Both now key off
`behavioral_duration_years`: repricing_schedule spreads the balance over an
equivalent-average-life ladder (same convention as `laddered` positions), so
the gap and EVE reports are finally consistent with each other.
"""

from dataclasses import dataclass, field
from typing import Optional

import numpy as np

import config


@dataclass
class Position:
    name: str
    side: str              # "asset" or "liability"
    category_type: str     # "variable" | "administered" | "fixed_amortizing" | "laddered"
    balance: float          # starting balance, $
    rate: float              # starting annualized rate, decimal (e.g. 0.065)
    index: str = "SHORT"       # rate index this position reprices off (more index types added in a future iteration)
    beta: float = 1.0            # repricing sensitivity to benchmark rate moves
    lag_months: int = 0           # administered-rate repricing lag (rate response timing, not cashflow timing)
    spread: float = 0.0             # spread to benchmark used when pricing new/renewed volume
    cpr_annual: float = 0.0           # constant prepayment/runoff rate (fixed_amortizing)
    ladder_months: int = 12           # average maturity, for laddered positions
    growth_rate_annual: float = 0.0    # net organic balance growth (on top of decay/roll)
    rate_floor: Optional[float] = None
    behavioral_duration_years: Optional[float] = None  # NMD effective EVE/gap duration override
    liquidity_decay_annual: Optional[float] = None      # core-deposit attrition rate for liquidity view
    reset_frequency_months: int = 1  # variable positions: months between rate resets
    seasonal: bool = False   # if True, growth is multiplied by SEASONALITY_INDEX_DEPOSITS each month
    plug: bool = False          # this position absorbs the monthly balance-sheet funding gap
    cash_sink: bool = False       # this position absorbs surplus cash when the funding gap is negative

    def _ladder_months_for_duration(self) -> int:
        """Uniform-ladder length whose average life ((n+1)/2 months) equals behavioral_duration_years."""
        target_months = round(2 * self.behavioral_duration_years * 12 - 1)
        return max(1, target_months)

    def repricing_schedule(self, max_months: int = config.ALM_MAX_MONTHS):
        """Dollars of current balance that reprice in each month 0..max_months-1, plus leftover."""
        schedule = np.zeros(max_months)

        if self.category_type == "variable":
            schedule[0] = self.balance

        elif self.category_type == "administered":
            if self.behavioral_duration_years is not None:
                n = min(self._ladder_months_for_duration(), max_months)
                piece = self.balance / self._ladder_months_for_duration()
                for t in range(n):
                    schedule[t] = piece
            else:
                idx = min(self.lag_months, max_months - 1)
                schedule[idx] += self.balance

        elif self.category_type == "fixed_amortizing":
            cpr_m = 1 - (1 - self.cpr_annual) ** (1 / 12) if self.cpr_annual > 0 else 0.0
            remaining = self.balance
            for t in range(max_months):
                runoff = remaining * cpr_m
                schedule[t] = runoff
                remaining -= runoff

        elif self.category_type == "laddered":
            n = min(self.ladder_months, max_months)
            piece = self.balance / self.ladder_months
            for t in range(n):
                schedule[t] = piece

        else:
            raise ValueError(f"Unknown category_type: {self.category_type}")

        leftover = max(0.0, self.balance - schedule.sum())
        return schedule, leftover

    def cashflow_schedule(self, max_months: int = config.ALM_MAX_MONTHS):
        """Dollars of current balance that pay down/mature as cash in each future month, plus leftover."""
        if self.category_type != "administered":
            return self.repricing_schedule(max_months)

        decay = self.liquidity_decay_annual
        if decay is None:
            return self.repricing_schedule(max_months)

        decay_m = 1 - (1 - decay) ** (1 / 12)
        schedule = np.zeros(max_months)
        remaining = self.balance
        for t in range(max_months):
            runoff = remaining * decay_m
            schedule[t] = runoff
            remaining -= runoff
        leftover = max(0.0, self.balance - schedule.sum())
        return schedule, leftover
