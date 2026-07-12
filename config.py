"""
Default assumptions for the NIM forecasting model.

This is the single place to edit when you want to swap in your own bank's
numbers. If DATA_SOURCE="fdic", the balance sheet below is only used as a
fallback (if the FDIC pull fails); if DATA_SOURCE="synthetic", it's used as-is.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Bucket:
    name: str
    side: str              # "asset" or "liability"
    category_type: str     # "variable" | "fixed_amortizing" | "laddered" | "administered"
    balance: float          # starting balance, $
    rate: float              # starting annualized rate, decimal (e.g. 0.065)
    beta: float = 1.0        # repricing sensitivity to benchmark rate moves
    lag_months: int = 0       # administered-rate repricing lag
    spread: float = 0.0        # spread to benchmark used when pricing new/renewed volume
    cpr_annual: float = 0.0     # constant prepayment/runoff rate (fixed_amortizing)
    ladder_months: int = 12      # average maturity, for laddered buckets
    growth_rate_annual: float = 0.0  # net organic balance growth (on top of decay/roll)
    rate_floor: Optional[float] = None
    duration_years: Optional[float] = None  # behavioral effective duration override (administered buckets)
    liquidity_decay_annual: Optional[float] = None  # core-deposit attrition rate for liquidity view (administered buckets)
    reset_frequency_months: int = 1  # variable buckets: months between rate resets (e.g. 3 = quarterly reset, RLLR/MCLR-style). 1 = reprices every month.
    seasonal: bool = False  # if True, growth is multiplied by SEASONALITY_INDEX_DEPOSITS each month


# Reference/benchmark rate the whole book reprices off (proxy: effective Fed Funds).
# Overwritten by data_sources/fred_rates.py if a FRED API key is available.
STARTING_BENCHMARK_RATE = 0.0425  # 4.25%

# ---------------------------------------------------------------------------
# Synthetic mid-size commercial bank (~$5B earning assets), calibrated to
# ballpark ratios from FDIC Quarterly Banking Profile peer-group averages for
# banks in the $1B-$10B asset tier. Replace via data_sources/fdic_bank.py for
# a real institution (pass its FDIC certificate number).
# ---------------------------------------------------------------------------
DEFAULT_BUCKETS = [
    # ---- Earning assets ----
    Bucket("Fed funds sold / IB bank balances", "asset", "variable",
           balance=150_000_000, rate=0.0425, beta=1.00, spread=0.00, growth_rate_annual=0.00),
    Bucket("Investment securities", "asset", "laddered",
           balance=900_000_000, rate=0.0375, spread=0.0050, ladder_months=60, growth_rate_annual=0.02),
    # Quarterly reset (reset_frequency_months=3) - mirrors external-benchmark-linked floating
    # loans that reset on a fixed cadence (e.g. RLLR/MCLR-style 3-month reset) rather than
    # continuously; the rate is flat between reset dates, then jumps to catch up.
    Bucket("C&I loans (variable)", "asset", "variable",
           balance=1_100_000_000, rate=0.0750, beta=0.90, spread=0.0225, growth_rate_annual=0.04,
           rate_floor=0.03, reset_frequency_months=3),
    Bucket("CRE loans (fixed)", "asset", "fixed_amortizing",
           balance=1_600_000_000, rate=0.0625, spread=0.0200, cpr_annual=0.12, growth_rate_annual=0.03),
    Bucket("Residential mortgage", "asset", "fixed_amortizing",
           balance=700_000_000, rate=0.0575, spread=0.0150, cpr_annual=0.10, growth_rate_annual=0.01),
    Bucket("Consumer / other loans", "asset", "fixed_amortizing",
           balance=250_000_000, rate=0.0850, spread=0.0350, cpr_annual=0.20, growth_rate_annual=0.02),

    # ---- Interest-bearing liabilities ----
    # NOW/Savings/MMDA reprice (their coupon can change) almost immediately, but two other
    # behavioral assumptions apply on top of that repricing mechanic, and matter for
    # different reports:
    #   duration_years         - effective EVE duration; long, because balances are sticky.
    #   liquidity_decay_annual - core-deposit attrition/runoff rate; these are non-maturity
    #                            deposits, so a rate reset is NOT a cash outflow - only actual
    #                            balance attrition is. Used by the structural liquidity view,
    #                            never by the repricing gap (see model/alm_reports.py).
    #
    # Each CASA-style product is further split into "core" (stable, seasonally-patterned,
    # sticky - long duration, slow decay) and "non-core" (volatile/rate-shopped balances -
    # short duration, fast decay). This is standard non-maturity-deposit (NMD) behavioral
    # modeling: the core/non-core split (not just a single blended balance) is what actually
    # drives EVE duration and liquidity decay in practice. `seasonal=True` applies
    # SEASONALITY_INDEX_DEPOSITS to that bucket's monthly growth.
    Bucket("NOW - Core", "liability", "administered",
           balance=420_000_000, rate=0.0090, beta=0.15, lag_months=3, growth_rate_annual=0.02,
           duration_years=3.5, liquidity_decay_annual=0.02, seasonal=True),
    Bucket("NOW - Non-Core", "liability", "administered",
           balance=180_000_000, rate=0.0120, beta=0.40, lag_months=1, growth_rate_annual=0.01,
           duration_years=0.5, liquidity_decay_annual=0.25, seasonal=True),
    Bucket("Savings & MMDA - Core", "liability", "administered",
           balance=780_000_000, rate=0.0200, beta=0.30, lag_months=2, growth_rate_annual=0.03,
           duration_years=2.5, liquidity_decay_annual=0.05, seasonal=True),
    Bucket("Savings & MMDA - Non-Core", "liability", "administered",
           balance=520_000_000, rate=0.0260, beta=0.65, lag_months=1, growth_rate_annual=0.02,
           duration_years=0.4, liquidity_decay_annual=0.35, seasonal=True),
    Bucket("Time deposits (CDs)", "liability", "laddered",
           balance=900_000_000, rate=0.0400, spread=-0.0010, ladder_months=14, growth_rate_annual=0.02),
    Bucket("Short-term borrowings (repo/FHLB)", "liability", "variable",
           balance=200_000_000, rate=0.0450, beta=0.95, spread=0.0010, growth_rate_annual=0.00),
    Bucket("Long-term FHLB / sub debt", "liability", "laddered",
           balance=300_000_000, rate=0.0475, spread=0.0075, ladder_months=36, growth_rate_annual=0.00),
]

# Memo only - non-interest-bearing DDA. Doesn't touch the NIM calc directly but
# useful context for total funding mix / cost-of-funds reporting.
NONINTEREST_DDA_BALANCE = 700_000_000
NONINTEREST_DDA_GROWTH_ANNUAL = 0.015

HORIZON_MONTHS = 24

RATE_SCENARIOS = {
    "Base (flat)":     0.0,
    "+100 bps":        0.0100,
    "+200 bps":        0.0200,
    "-100 bps":       -0.0100,
    "-200 bps":       -0.0200,
}
RAMP_MONTHS = 12  # scenario shock is ramped in linearly over this many months

# ---------------------------------------------------------------------------
# ALM report assumptions (rate sensitivity gap, duration/EVE, structural
# liquidity, earnings-at-risk) - see model/alm_reports.py
# ---------------------------------------------------------------------------

# Fallback total equity capital, used for EVE-as-%-of-capital when a real
# bank's reported equity isn't available (synthetic balance sheet run).
# ~10% of earning assets, a typical well-capitalized community bank ratio.
EQUITY_CAPITAL_FALLBACK = 470_000_000

# Static gap/liquidity reports bucket repricing/maturity cashflows into these
# standard ALM time bands (upper bound in months, exclusive; last band is open-ended).
ALM_TIME_BANDS = [
    ("0-1M", 0, 1),
    ("1-3M", 1, 3),
    ("3-6M", 3, 6),
    ("6-12M", 6, 12),
    ("1-3Y", 12, 36),
    ("3-5Y", 36, 60),
]
ALM_MAX_MONTHS = 60  # anything not repriced/matured by here falls into the ">5Y" band

# Illustrative internal policy limit: cumulative liquidity gap shouldn't go more
# negative than this, as a fraction of total assets, within the near-term bands.
# (Real banks set this via internal ALCO policy / regulatory guidance - this is
# a reasonable illustrative threshold, not a specific regulatory citation.)
LIQUIDITY_GAP_TOLERANCE_PCT_ASSETS = -0.10

# ---------------------------------------------------------------------------
# Deposit seasonality - illustrative monthly index (Jan=index 0) applied to
# growth_rate_annual for buckets with seasonal=True (the CASA buckets above).
# A real bank derives this from time-series analysis of historical balances
# by product; these are illustrative multipliers (e.g. Q4 inflows, Q1 dip).
# ---------------------------------------------------------------------------
SEASONALITY_INDEX_DEPOSITS = [
    1.00, 0.95, 0.95, 1.05, 1.05, 1.00,   # Jan-Jun
    0.95, 0.95, 1.00, 1.05, 1.10, 1.15,   # Jul-Dec
]

# ---------------------------------------------------------------------------
# Funds Transfer Pricing (FTP) curve - see model/ftp.py
#
# Matched-maturity FTP: each bucket is charged/credited a transfer rate =
# benchmark rate (at that month) + a spread that increases with the bucket's
# assigned tenor (its effective duration/average life - see
# alm_reports.bucket_effective_duration, reused here so "the tenor a bucket
# is FTP'd at" matches "the tenor used for its EVE duration", consistent with
# real matched-maturity FTP methodology).
#
# This spread curve is a simplified stand-in for a real OIS/swap-curve-implied
# forward curve; tune it to reflect your own funding curve.
# ---------------------------------------------------------------------------
FTP_CURVE_SPREADS_BY_TENOR_YEARS = {
    0.08: 0.0000,   # ~1 month
    0.25: 0.0005,   # 3 months
    0.5:  0.0010,   # 6 months
    1:    0.0020,
    2:    0.0035,
    3:    0.0045,
    5:    0.0060,
    10:   0.0080,
}

# Management overlay: a purely curve-implied FTP can price short-tenor funding too
# low during an easing cycle (this happened in practice - see README); floor the
# spread for tenors under this cutoff at this minimum.
FTP_SHORT_TENOR_CUTOFF_YEARS = 1.0
FTP_SHORT_TENOR_MIN_SPREAD = 0.0010
