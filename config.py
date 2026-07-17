"""
Default assumptions for the NIM/ALM model.

The balance sheet itself lives in `balance_sheet.yaml` (loaded via
`core.balance_sheet.load_positions`), not here - this module holds the
model-wide assumptions that apply across the whole balance sheet: the base
curve, rate scenarios, ALM reporting bands, and the FTP policy curve.
"""

from curve import shocks
from curve.basis import BasisOverlay

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
# curve/shocks.py for steepener/flattener/twist/custom builders. A scenario
# can also be a (shift_fn, basis_shocks) tuple: basis_shocks is
# {index_name: bps_shift}, ramped the same way as the curve shift, applied
# to that index's overlay in INDEX_BASIS below (see curve/scenarios.py and
# core/indices.py) instead of the base curve itself - a basis shock moves
# what an index projects to without moving the discount curve at all.
RATE_SCENARIOS = {
    "Base (flat)": shocks.parallel(0),
    "+100 bps": shocks.parallel(100),
    "+200 bps": shocks.parallel(200),
    "-100 bps": shocks.parallel(-100),
    "-200 bps": shocks.parallel(-200),
    # Every SHORT-indexed position (Fed funds sold, cash, short-term
    # wholesale borrowings) reprices 25bps higher relative to the base
    # curve, base curve itself unchanged: a funding-market stress, not a Fed
    # move. On the default (deposit-funded, thin wholesale) book this is
    # NIM-accretive, not dilutive - SHORT-indexed assets ($230M Fed funds
    # sold + cash) outweigh the small SHORT-indexed liability (the plug,
    # near-zero when the book runs surplus-funded) - a real, mix-dependent
    # result, not the naive "wider funding cost always hurts NIM" intuition.
    "Funding basis widening": (shocks.parallel(0), {"SHORT": 25}),
    # Every TENOR/FIXED-priced new production (fixed-rate loans, laddered
    # securities and term funding) reprices at a tighter spread to the base
    # curve: a credit/liquidity spread compression across term-priced
    # production, broader than "loans" alone since TENOR/FIXED is shared by
    # the whole fixed-rate book, not narrowed further in this model.
    "Term-priced spread compression": (shocks.parallel(0), {"TENOR": -50, "FIXED": -50}),
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

# Per-index basis overlay (curve/basis.py): added to the base curve's spot
# rate when index_rate() projects that index, on top of whatever a
# scenario's basis_shocks adds. None (or an all-zero overlay) means an
# index reads the base curve directly, today's behavior. Only SHORT, TENOR,
# FIXED and TBILL3M are resolved directly by index_rate(); ADMIN and MCLR
# are engine-driven (core/products/administered.py, core/indices.py::
# compute_mclr) and do not consult this table.
ZERO_BASIS_OVERLAY = BasisOverlay({0.0: 0.0, 10.0: 0.0})
INDEX_BASIS = {
    # Illustrative, small: SOFR/Fed-Funds-effective-style overnight funding
    # typically prices a few bps under a generic short curve point.
    "SHORT": BasisOverlay({0.0: -0.0003, 10.0: -0.0003}),
    "TENOR": None,
    "FIXED": None,
    # Basis spread between a 3-month T-bill and the OIS/benchmark curve at
    # the same tenor (T-bills typically yield a bit below OIS-implied
    # rates). Migrated from the old flat TBILL_OIS_BASIS_SPREAD constant;
    # same value, now routed through the general per-index mechanism.
    "TBILL3M": BasisOverlay({0.25: -0.0005, 10.0: -0.0005}),
}

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

# ---------------------------------------------------------------------------
# Liquidity Coverage Ratio (LCR) - see core/lcr.py. Basel III standard
# run-off/haircut factors (BCBS238, Jan 2013), simplified to the categories
# this balance sheet actually uses.
# ---------------------------------------------------------------------------

# HQLA haircuts by level: LCR_HQLA_HAIRCUTS[position.hqla_level].
LCR_HQLA_HAIRCUTS = {
    "L1": 0.00,
    "L2A": 0.15,
    "L2B": 0.25,
}
LCR_L2B_CAP_OF_HQLA = 0.15   # L2B (after haircut) capped at 15% of total HQLA
LCR_L2_CAP_OF_HQLA = 0.40    # L2A + L2B (after haircut/cap) capped at 40% of total HQLA

# 30-day outflow run-off rates by lcr_outflow_category: applied to the full
# balance for administered/variable liabilities (no contractual maturity, or
# already short-term); applied only to the current month's maturing slice
# (Position.cashflow_schedule()[0]) for laddered liabilities.
LCR_OUTFLOW_FACTORS = {
    "stable_retail": 0.05,
    "less_stable_retail": 0.10,
    "term_deposit": 0.10,             # laddered term deposits maturing within 30 days
    "wholesale_operational": 0.25,
    "wholesale_non_operational": 0.40,
}

# Contractual inflow factor for fully performing loans (fixed_amortizing
# assets' current month's scheduled runoff), capped at 75% of gross outflows
# (net outflows can never fall below 25% of gross - the max() in compute_lcr).
LCR_PERFORMING_LOAN_INFLOW_FACTOR = 0.50
LCR_INFLOW_CAP_PCT_OF_OUTFLOWS = 0.75

# LCR targets, drawn as horizontal lines on the LCR-vs-target chart.
LCR_REGULATORY_MIN = 1.00
LCR_RAS_THRESHOLD = 1.10
LCR_INTERNAL_TARGET = 1.15

# ---------------------------------------------------------------------------
# AFS mark-to-market buffer - see core/mtm.py. Unrealized AFS gains
# realizable for sale, capped as a fraction of the HTM book (RBI-style
# trading-book limit).
# ---------------------------------------------------------------------------
TRADING_LIMIT_PCT = 0.05

# Flat policy spread over the overnight rate for core/ftp/straight_spread.py
# (no tenor/duration lookup - a simple fallback method).
FTP_STRAIGHT_SPREAD = 0.0025
