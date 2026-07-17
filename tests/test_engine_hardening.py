"""
Acceptance tests for two latent logic gaps fixed together:

1. Plug/cash_sink netting (core/engine.py): previously each position's
   balance only ever grew (the plug on deficit months, the sink on surplus
   months), so a scenario that flipped between deficit and surplus left
   both wholesale borrowings and surplus cash elevated side by side forever.
   The fix nets a month's adjustment against the OTHER position's existing
   balance first.
2. Negative growth_rate_annual silently discarded (core/products/
   fixed_amortizing.py, core/products/laddered.py): new production was
   floored at zero before adding scheduled runoff, so a negative growth
   assumption never actually shrank the book beyond its CPR/maturity decay.
   The fix floors the total (runoff plus growth) instead.

Run with: py -m pytest tests/ -q
"""

import config
from curve import shocks
from curve.yield_curve import YieldCurve
from curve.scenarios import build_curve_scenarios
from core.position import Position
from core.products import fixed_amortizing, laddered
from core import engine


# ---------------------------------------------------------------------------
# 1. Plug and cash_sink net against each other instead of both growing
#    forever once the funding gap flips sign.
# ---------------------------------------------------------------------------

def test_plug_and_sink_net_against_each_other_across_a_gap_flip():
    loan = Position(
        name="Loan", side="asset", category_type="variable",
        balance=400_000_000, rate=0.05, index="SHORT", growth_rate_annual=0.0,
    )
    cd = Position(
        name="CD", side="liability", category_type="laddered",
        balance=150_000_000, rate=0.03, index="TENOR", origination_tenor_years=0.5,
        ladder_months=6, renewal_rate=0.0, growth_rate_annual=0.0,
    )
    deposit = Position(
        name="Deposit", side="liability", category_type="administered",
        balance=50_000_000, rate=0.02, index="ADMIN", beta=0.5, growth_rate_annual=3.0,
    )
    plug = Position(
        name="Plug", side="liability", category_type="variable",
        balance=0.0, rate=0.045, index="SHORT", growth_rate_annual=0.0, plug=True,
    )
    cash_sink = Position(
        name="CashSink", side="asset", category_type="variable",
        balance=0.0, rate=0.0425, index="SHORT", growth_rate_annual=0.0, cash_sink=True,
    )

    base_curve = YieldCurve(config.FALLBACK_CURVE_TENORS, config.FALLBACK_CURVE_RATES)
    paths = build_curve_scenarios(base_curve, {"Base": shocks.parallel(0)}, horizon_months=24, ramp_months=12)
    summary, detail, _ = engine.run_scenario(
        [loan, cd, deposit, plug, cash_sink], paths["Base"], scenario_label="Base",
    )

    plug_by_month = {}
    sink_by_month = {}
    for m in range(24):
        month_detail = detail[detail["month"] == m]
        plug_by_month[m] = month_detail.loc[month_detail["bucket"] == "Plug", "balance"].iloc[0]
        sink_by_month[m] = month_detail.loc[month_detail["bucket"] == "CashSink", "balance"].iloc[0]

    # The CD's fast runoff (6-month ladder, no renewal) creates an early
    # funding deficit; the deposit's aggressive growth later creates a
    # persistent surplus. Both regimes must actually occur for this test
    # to exercise the fix.
    assert max(plug_by_month.values()) > 1_000_000
    assert max(sink_by_month.values()) > 1_000_000

    # Netting invariant: at any given month, at most one of the two is
    # meaningfully above its (zero) starting balance. The old code let both
    # grow independently once the gap direction flipped.
    for m in range(24):
        assert min(plug_by_month[m], sink_by_month[m]) < 1.0, f"plug and sink coexisted at month {m}"

    # Once the surplus regime is well established, the plug (built up during
    # the earlier deficit) must have been paid back down, not left standing.
    assert plug_by_month[23] < 1.0
    assert sink_by_month[23] > 1_000_000


# ---------------------------------------------------------------------------
# 2a. fixed_amortizing: negative growth_rate_annual shrinks the book below
#     what scheduled CPR runoff alone would produce, instead of being
#     silently floored to zero.
# ---------------------------------------------------------------------------

def test_fixed_amortizing_negative_growth_shrinks_book_beyond_scheduled_runoff():
    curve = YieldCurve([1 / 12, 1.0, 5.0, 10.0], [0.04, 0.04, 0.04, 0.04])
    p = Position(
        name="Loan", side="asset", category_type="fixed_amortizing",
        balance=100_000_000, rate=0.05, index="FIXED", origination_tenor_years=5,
        spread=0.0, cpr_annual=0.12, growth_rate_annual=-0.06,
    )
    cohorts = fixed_amortizing.seed(p, curve)
    balances = [sum(c.balance for c in cohorts)]
    for t in range(1, 24):
        fixed_amortizing.step(p, cohorts, curve, t)
        balances.append(sum(c.balance for c in cohorts))

    for i in range(1, len(balances)):
        assert balances[i] <= balances[i - 1] + 1e-6

    # Under the old (buggy) code, new_production always exactly replaced
    # scheduled runoff whenever growth was negative, so the book stayed
    # perfectly flat. Assert it meaningfully shrank instead.
    assert balances[-1] < balances[0] * 0.95


# ---------------------------------------------------------------------------
# 2b. laddered: negative growth_rate_annual rolls over less than the
#     maturing slice, instead of always fully renewing it.
# ---------------------------------------------------------------------------

def test_laddered_negative_growth_shrinks_rollover_below_maturing_amount():
    curve = YieldCurve([1 / 12, 1.0, 5.0, 10.0], [0.04, 0.04, 0.04, 0.04])
    p = Position(
        name="CD", side="liability", category_type="laddered",
        balance=100_000_000, rate=0.03, index="TENOR", origination_tenor_years=0.5,
        ladder_months=12, renewal_rate=1.0, growth_rate_annual=-0.12,
    )
    balance, rate = p.balance, p.rate
    balances = [balance]
    for t in range(1, 24):
        balance, rate, _, _ = laddered.step(p, balance, rate, curve, t)
        balances.append(balance)

    for i in range(1, len(balances)):
        assert balances[i] <= balances[i - 1] + 1e-6

    # Under the old code, renewal_rate=1.0 plus a discarded negative growth
    # meant renewed always exactly replaced the maturing slice, so the book
    # stayed perfectly flat. Assert it meaningfully shrank instead.
    assert balances[-1] < balances[0] * 0.95
