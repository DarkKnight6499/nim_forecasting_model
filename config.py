"""
Default assumptions for the NIM/ALM model.

The balance sheet itself lives in `balance_sheet.yaml` (loaded via
`core.balance_sheet.load_positions`), not here - this module holds the
model-wide assumptions that apply across the whole balance sheet: the base
curve, rate scenarios, ALM reporting bands, and the FTP policy curve.
"""

from curve import shocks

# ---------------------------------------------------------------------------
# Base yield curve
# ---------------------------------------------------------------------------

# Anchor for the short end when no FRED key is available (proxy: effective Fed Funds).
STARTING_BENCHMARK_RATE = 0.0425  # 4.25%

# Illustrative upward-sloping fallback curve shape, used if the live Treasury
# daily par yield curve fetch fails. Re-anchored at runtime so its short end
# matches STARTING_BENCHMARK_RATE (or a FRED-sourced anchor) - see
# data_sources/treasury_curve.py:get_base_curve.
FALLBACK_CURVE_TENORS = [1 / 12, 0.25, 0.5, 1.0, 2.0, 3.0, 5.0, 10.0]
FALLBACK_CURVE_RATES = [0.0425, 0.0420, 0.0405, 0.0385, 0.0375, 0.0380, 0.0400, 0.0430]

HORIZON_MONTHS = 24

# Each scenario is a curve shift function (tenor_years -> decimal shift), not a
# flat scalar - this is what makes non-parallel scenarios possible. See
# curve/shocks.py for steepener/flattener/twist/custom builders.
RATE_SCENARIOS = {
    "Base (flat)": shocks.parallel(0),
    "+100 bps": shocks.parallel(100),
    "+200 bps": shocks.parallel(200),
    "-100 bps": shocks.parallel(-100),
    "-200 bps": shocks.parallel(-200),
}
RAMP_MONTHS = 12  # scenario shock is ramped in linearly over this many months

# ---------------------------------------------------------------------------
# Balance sheet identity (see core/engine.py)
# ---------------------------------------------------------------------------

# Fraction of each month's net interest income retained into equity (vs paid
# out as dividends). 1.0 = fully retained.
RETENTION_RATIO = 1.0

# ---------------------------------------------------------------------------
# Rate indices (see core/indices.py)
# ---------------------------------------------------------------------------

# MCLR = MCLR_DEPOSIT_WEIGHT * deposit cost + (1 - MCLR_DEPOSIT_WEIGHT) *
# borrowing cost + MCLR_EQUITY_SPREAD. See core/indices.py::compute_mclr.
MCLR_DEPOSIT_WEIGHT = 0.7
MCLR_EQUITY_SPREAD = 0.0025

# Basis spread between a 3-month T-bill and the OIS/benchmark curve at the
# same tenor (T-bills typically yield a bit below OIS-implied rates).
TBILL_OIS_BASIS_SPREAD = -0.0005

# Cap on rate-dependent effective CPR for amortizing assets (refi burnout).
CPR_MAX_DEFAULT = 0.40

# Memo only - non-interest-bearing DDA. Doesn't touch the NIM calc directly but
# useful context for total funding mix / cost-of-funds reporting.
NONINTEREST_DDA_BALANCE = 700_000_000
NONINTEREST_DDA_GROWTH_ANNUAL = 0.015

# ---------------------------------------------------------------------------
# ALM report assumptions (rate sensitivity gap, duration/EVE, structural
# liquidity, earnings-at-risk) - see model/alm_reports.py
# ---------------------------------------------------------------------------

# Fallback total equity, used when a real bank's reported equity isn't available.
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

# Cumulative liquidity gap tolerance, as a fraction of total assets.
LIQUIDITY_GAP_TOLERANCE_PCT_ASSETS = -0.10

# ---------------------------------------------------------------------------
# Deposit seasonality - monthly index (Jan=index 0) applied to growth_rate_annual
# for positions with seasonal=True.
# ---------------------------------------------------------------------------
SEASONALITY_INDEX_DEPOSITS = [
    1.00, 0.95, 0.95, 1.05, 1.05, 1.00,   # Jan-Jun
    0.95, 0.95, 1.00, 1.05, 1.10, 1.15,   # Jul-Dec
]

# ---------------------------------------------------------------------------
# FTP policy - see core/ftp/. A position's FTP rate is the yield curve read at
# a method-specific tenor (core/ftp/matched_maturity.py, pooled_replicating.py)
# plus a liquidity-premium spread read off this table at that same tenor.
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

# Floors the FTP spread at FTP_SHORT_TENOR_MIN_SPREAD for tenors below this cutoff.
FTP_SHORT_TENOR_CUTOFF_YEARS = 1.0
FTP_SHORT_TENOR_MIN_SPREAD = 0.0010

# Flat policy spread over the overnight rate for core/ftp/straight_spread.py
# (no tenor/duration lookup - a simple fallback method).
FTP_STRAIGHT_SPREAD = 0.0025
