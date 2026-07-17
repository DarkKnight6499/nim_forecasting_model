"""
Acceptance tests for liability-side behavioral modeling: NMD decay estimation
(core/nmd_estimation.py), TD renewal realism (Position.renewal_rate), and
deposit price elasticity (core/elasticity.py).
Run with: py -m pytest tests/ -q
"""

import pytest

import config
from curve import shocks
from curve.yield_curve import YieldCurve
from curve.scenarios import build_curve_scenarios
from core.balance_sheet import load_positions
from core.position import Position
from core.products import administered
from core import engine
from core.nmd_estimation import estimate_decay
from core.elasticity import breakeven_rate_uplift
from data_sources.synthetic_deposits import generate_balance_history


# ---------------------------------------------------------------------------
# 1. estimate_decay recovers known synthetic parameters within tolerance.
# ---------------------------------------------------------------------------

def test_estimate_decay_recovers_known_parameters():
    true_core_fraction, true_decay_core, true_decay_noncore = 0.65, 0.15, 0.80
    history = generate_balance_history(
        core_fraction=true_core_fraction, decay_annual_core=true_decay_core,
        decay_annual_noncore=true_decay_noncore, months=36, seed=42,
    )
    result = estimate_decay(history["balance"])

    assert abs(result["core_fraction"] - true_core_fraction) / true_core_fraction < 0.10
    assert abs(result["decay_annual_core"] - true_decay_core) / true_decay_core < 0.25
    assert abs(result["decay_annual_noncore"] - true_decay_noncore) / true_decay_noncore < 0.25


# ---------------------------------------------------------------------------
# 2. TD renewal_rate < 1.0: less of the maturing book rolls over, the plug
#    absorbs the difference, and the balance sheet identity still holds.
#    Uses a small synthetic book (not balance_sheet.yaml) so the test isn't
#    coupled to the production book's overall surplus/deficit direction,
#    which shifts whenever positions are added/resized there.
# ---------------------------------------------------------------------------

def _synthetic_book(cd_renewal_rate):
    loan = Position(
        name="Loan", side="asset", category_type="fixed_amortizing",
        balance=1_000_000_000, rate=0.06, index="FIXED", origination_tenor_years=5,
        spread=0.02, cpr_annual=0.05, growth_rate_annual=0.05,
    )
    cd = Position(
        name="CD", side="liability", category_type="laddered",
        balance=600_000_000, rate=0.03, index="TENOR", origination_tenor_years=0.625,
        ladder_months=14, growth_rate_annual=0.0, renewal_rate=cd_renewal_rate,
    )
    plug = Position(
        name="Plug", side="liability", category_type="variable",
        balance=100_000_000, rate=0.045, index="SHORT", growth_rate_annual=0.0, plug=True,
    )
    cash_sink = Position(
        name="CashSink", side="asset", category_type="variable",
        balance=50_000_000, rate=0.0425, index="SHORT", growth_rate_annual=0.0, cash_sink=True,
    )
    return [loan, cd, plug, cash_sink]


def test_td_renewal_rate_reduces_rollover_and_plug_absorbs_shortfall():
    base_curve = YieldCurve(config.FALLBACK_CURVE_TENORS, config.FALLBACK_CURVE_RATES)
    paths = build_curve_scenarios(base_curve, {"Base": shocks.parallel(0)}, horizon_months=24, ramp_months=12)

    summary_full, detail_full, _ = engine.run_scenario(_synthetic_book(1.0), paths["Base"], scenario_label="full")
    summary_partial, detail_partial, _ = engine.run_scenario(_synthetic_book(0.85), paths["Base"], scenario_label="partial")

    def plug_balance(detail_df, month):
        row = detail_df[(detail_df["bucket"] == "Plug") & (detail_df["month"] == month)]
        return row["balance"].iloc[0]

    assert plug_balance(detail_partial, 23) > plug_balance(detail_full, 23)

    for summary_df, detail_df in [(summary_full, detail_full), (summary_partial, detail_partial)]:
        for month in range(1, 24):
            month_detail = detail_df[detail_df["month"] == month]
            assets = month_detail.loc[month_detail["side"] == "asset", "balance"].sum()
            liabs = month_detail.loc[month_detail["side"] == "liability", "balance"].sum()
            equity = summary_df.loc[summary_df["month"] == month, "equity"].iloc[0]
            assert abs(assets - liabs - equity) < 1.0, f"identity violated at month {month}"


# ---------------------------------------------------------------------------
# 3. Elasticity: a lagging (beta < 1) administered rate under +200bps loses
#    volume relative to the no-elasticity case, strictly, from month 6 on.
# ---------------------------------------------------------------------------

def test_elasticity_reduces_growth_when_rate_lags_market():
    base_curve = YieldCurve([1 / 12, 1.0, 10.0], [0.03, 0.035, 0.045])
    paths = build_curve_scenarios(base_curve, {"+200 bps": shocks.parallel(200)}, horizon_months=24, ramp_months=12)
    path = paths["+200 bps"]

    def simulate(pricing_elasticity):
        p = Position(
            name="Test CASA", side="liability", category_type="administered",
            balance=100_000_000, rate=0.02, index="ADMIN", beta=0.3, lag_months=1,
            growth_rate_annual=0.05, pricing_elasticity=pricing_elasticity,
        )
        balance, rate = p.balance, p.rate
        balances = [balance]
        for t in range(1, len(path.curves)):
            lag_idx = max(0, t - p.lag_months)
            balance, rate = administered.step(p, balance, rate, path.curves[0], path.curves[lag_idx], path.curves[t], t)
            balances.append(balance)
        return balances

    balances_elastic = simulate(pricing_elasticity=5.0)
    balances_flat = simulate(pricing_elasticity=0.0)

    for m in range(6, 24):
        assert balances_elastic[m] < balances_flat[m]


# ---------------------------------------------------------------------------
# 4. breakeven_rate_uplift: verify cost-with-uplift equals cost-without at
#    the point it returns.
# ---------------------------------------------------------------------------

def test_breakeven_rate_uplift_is_cost_neutral_at_its_own_answer():
    position = Position(
        name="Test CASA", side="liability", category_type="administered",
        balance=200_000_000, rate=0.02, index="ADMIN", beta=0.3, pricing_elasticity=50.0,
    )
    curve = YieldCurve([1 / 12, 1.0, 10.0], [0.045, 0.045, 0.045])
    breakeven_rate = breakeven_rate_uplift(position, curve, target_volume_uplift=0.05)

    delta = breakeven_rate - position.rate
    market_rate = curve.spot(1 / 12)
    extra_volume = position.balance * position.pricing_elasticity * delta
    new_balance = position.balance + extra_volume

    cost_with_uplift = new_balance * breakeven_rate
    cost_without = position.balance * position.rate + extra_volume * market_rate
    assert cost_with_uplift == pytest.approx(cost_without, abs=1.0)
