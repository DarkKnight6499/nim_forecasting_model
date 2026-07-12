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

# Memo only - non-interest-bearing DDA. Doesn't touch the NIM calc directly but
# useful context for total funding mix / cost-of-funds reporting.
NONINTEREST_DDA_BALANCE = 700_000_000
NONINTEREST_DDA_GROWTH_ANNUAL = 0.015

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
# forward curve; tune it to reflect your own funding curve. A future iteration
# should replace the floating-rate mechanic here with true origination-locked
# matched-maturity FTP.
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
