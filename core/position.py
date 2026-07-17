"""
Position: a balance-sheet line item with two schedule methods, used by the
gap, liquidity, duration/EVE, and FTP reports.

  repricing_schedule(max_months)  - dollars of current balance whose RATE
                                     resets in each future month.
  cashflow_schedule(max_months)   - dollars of current balance that mature/
                                     pay down as cash in each future month.

For administered positions, repricing_schedule spreads the balance over an
equivalent-average-life ladder sized from behavioral_duration_years (same
convention as laddered positions); cashflow_schedule instead decays it at
liquidity_decay_annual. Every other category_type has one real cashflow
timing, so cashflow_schedule reuses repricing_schedule directly.
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
    index: str = "SHORT"       # rate index this position reprices off: SHORT | TENOR | FIXED | ADMIN | MCLR (see core/indices.py)
    beta: float = 1.0            # repricing sensitivity to index moves (SHORT/TENOR/ADMIN; ignored for MCLR)
    lag_months: int = 0           # administered-rate repricing lag (rate response timing, not cashflow timing)
    spread: float = 0.0             # spread to index used when pricing new/renewed/reset volume
    cpr_annual: float = 0.0           # base constant prepayment/runoff rate (fixed_amortizing)
    ladder_months: int = 12           # average maturity, for laddered positions
    growth_rate_annual: float = 0.0    # net organic balance growth (on top of decay/roll)
    rate_floor: Optional[float] = None
    behavioral_duration_years: Optional[float] = None  # NMD effective EVE/gap duration override
    liquidity_decay_annual: Optional[float] = None      # core-deposit attrition rate for liquidity view
    reset_frequency_months: int = 1  # variable positions: months between rate resets (staggered across that many cohorts)
    seasonal: bool = False   # if True, growth is multiplied by SEASONALITY_INDEX_DEPOSITS each month
    plug: bool = False          # this position absorbs the monthly balance-sheet funding gap
    cash_sink: bool = False       # this position absorbs surplus cash when the funding gap is negative
    origination_tenor_years: float = 0.0   # curve tenor new production is priced at (TENOR/FIXED index)
    refi_sensitivity: float = 0.0            # extra annual CPR per 1.0 (100%) of coupon-vs-market-rate refi incentive
    cpr_max: float = 0.40                      # cap on rate-dependent effective CPR (fixed_amortizing)
    feeds_mclr_deposit_cost: bool = False        # this position's new production counts toward the MCLR deposit-cost input
    ftp_method: str = "matched_maturity"          # transfer-pricing method key (see core/ftp/registry.py)
    renewal_rate: float = 1.0                      # laddered: fraction of each maturing slice that rolls into new production
    early_withdrawal_annual: float = 0.0             # laddered: extra annual runoff on the not-yet-matured balance
    pricing_elasticity: float = 0.0                    # administered: volume response to (own rate - market rate), see core/elasticity.py
    hqla_level: Optional[str] = None                     # assets: "L1" | "L2A" | "L2B" HQLA classification, see core/lcr.py
    lcr_outflow_category: Optional[str] = None             # liabilities: config.LCR_OUTFLOW_FACTORS key, see core/lcr.py
    accounting: Optional[str] = None                         # investment assets: "HTM" | "AFS", see core/mtm.py
    calibration_category: Optional[str] = None                # one of core.balance_sheet.CALIBRATION_CATEGORIES, see data_sources/fdic_bank.py

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


def bucket_effective_duration(position: Position, benchmark_rate: float) -> float:
    """Effective (modified) duration in years for one position."""
    if position.category_type == "variable":
        return 1 / 12
    if position.category_type == "administered":
        if position.behavioral_duration_years is not None:
            return position.behavioral_duration_years
        return max(position.lag_months / 12, 1 / 12)
    if position.category_type == "fixed_amortizing":
        avg_life = (1 / position.cpr_annual) if position.cpr_annual > 0 else 30.0
        avg_life = min(avg_life, 30.0)
        return avg_life / (1 + benchmark_rate)
    if position.category_type == "laddered":
        avg_life = (position.ladder_months + 1) / 2 / 12
        return avg_life / (1 + benchmark_rate)
    raise ValueError(f"Unknown category_type: {position.category_type}")
