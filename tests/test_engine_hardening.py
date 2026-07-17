"""
Acceptance tests for four latent logic gaps fixed together:

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
3. cost_of_ib_liabilities computed on end-of-month liability balances
   (core/engine.py) while every other summary rate (asset yield, NIM) used
   average monthly balances. The fix tracks an average liability balance
   the same way average earning assets already were.
4. Pooled-replicating FTP (core/ftp/pooled_replicating.py) modeled the
   rolling ladder as exponential smoothing, which gives the day-0 backfill
   rate infinite (geometrically decaying, never zero) memory instead of the
   true n-month ladder forgetting it completely once n months have rolled.
   The fix tracks an actual deque of n tranche rates.

Run with: py -m pytest tests/ -q
"""

import pytest

import config
from curve import shocks
from curve.yield_curve import YieldCurve
from curve.scenarios import build_curve_scenarios
from core.position import Position
from core.products import fixed_amortizing, laddered
from core import engine
from core.ftp import pooled_replicating
from core.ftp.spread_curve import spread_for_tenor


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


# ---------------------------------------------------------------------------
# 3. cost_of_ib_liabilities is computed on average (not end-of-month)
#    liability balances, consistent with yield_on_earning_assets and NIM.
# ---------------------------------------------------------------------------

def test_cost_of_funds_uses_average_not_end_of_month_liability_balances():
    loan = Position(
        name="Loan", side="asset", category_type="variable",
        balance=200_000_000, rate=0.05, index="SHORT", beta=0.0, growth_rate_annual=0.0,
    )
    deposit = Position(
        name="Deposit", side="liability", category_type="variable",
        balance=100_000_000, rate=0.03, index="SHORT", beta=0.0, growth_rate_annual=1.2,
    )
    plug = Position(
        name="Plug", side="liability", category_type="variable",
        balance=0.0, rate=0.045, index="SHORT", growth_rate_annual=0.0, plug=True,
    )
    cash_sink = Position(
        name="CashSink", side="asset", category_type="variable",
        balance=0.0, rate=0.0425, index="SHORT", growth_rate_annual=0.0, cash_sink=True,
    )

    base_curve = YieldCurve([1 / 12, 1.0, 5.0, 10.0], [0.04, 0.04, 0.04, 0.04])
    paths = build_curve_scenarios(base_curve, {"Base": shocks.parallel(0)}, horizon_months=6, ramp_months=3)
    summary, detail, _ = engine.run_scenario(
        [loan, deposit, plug, cash_sink], paths["Base"], scenario_label="Base",
    )

    diverged_at_least_once = False
    for m in range(1, 6):
        month_liab = detail[(detail["month"] == m) & (detail["side"] == "liability")]
        prev_liab = detail[(detail["month"] == m - 1) & (detail["side"] == "liability")].set_index("bucket")["balance"]

        avg_weighted_interest = 0.0
        avg_balance_total = 0.0
        eom_balance_total = 0.0
        for _, row in month_liab.iterrows():
            avg_bal = (prev_liab[row["bucket"]] + row["balance"]) / 2
            avg_weighted_interest += avg_bal * row["rate"] / 12
            avg_balance_total += avg_bal
            eom_balance_total += row["balance"]

        expected_avg_based = avg_weighted_interest * 12 / avg_balance_total
        actual = summary.loc[summary["month"] == m, "cost_of_ib_liabilities"].iloc[0]
        assert actual == pytest.approx(expected_avg_based, rel=1e-9)

        # The deposit's large monthly growth makes average and end-of-month
        # liability balances genuinely different; this is what the old
        # end-of-month-balance code would have produced instead.
        expected_eom_based = month_liab["interest"].sum() * 12 / eom_balance_total
        if abs(actual - expected_eom_based) > 1e-6:
            diverged_at_least_once = True

    assert diverged_at_least_once


# ---------------------------------------------------------------------------
# 4. Pooled-replicating FTP tracks a true n-month ladder: a tranche's
#    influence is exactly zero once n months have rolled, not merely decayed.
# ---------------------------------------------------------------------------

class _FakeCurvePath:
    def __init__(self, curves):
        self.curves = curves


def test_pooled_replicating_ladder_fully_forgets_day0_backfill_after_n_months():
    r0, r1 = 0.02, 0.06
    curve0 = YieldCurve([1 / 12, 1.0, 5.0, 10.0], [r0, r0, r0, r0])
    curve1 = YieldCurve([1 / 12, 1.0, 5.0, 10.0], [r1, r1, r1, r1])
    n = 4
    fake_path = _FakeCurvePath([curve0] + [curve1] * (n + 2))

    position = Position(
        name="CD", side="liability", category_type="laddered",
        balance=100_000_000, rate=0.03, index="TENOR", origination_tenor_years=n / 12,
        ladder_months=n,
    )

    rates = pooled_replicating.build_rate_series(position, fake_path, benchmark_rate_for_tenors=0.03)
    spread = spread_for_tenor(n / 12)

    assert rates[0] == pytest.approx(r0 + spread)
    # After n renewal months, every one of the n tranches has rolled at r1:
    # the day-0 backfill contributes exactly zero weight. Exponential
    # smoothing would instead leave a small but nonzero residual pull toward
    # r0 forever.
    assert rates[n] == pytest.approx(r1 + spread, abs=1e-9)
