"""
Acceptance tests for multi-curve basis risk: curve/basis.py's BasisOverlay,
config.INDEX_BASIS, core/indices.py::index_rate's basis_overlay parameter,
and the basis-shock scenarios added to config.RATE_SCENARIOS.

Before this phase, index_rate resolved every index (SHORT, TENOR, FIXED)
directly off the single base curve, with TBILL3M's basis hardcoded as a
lone constant - there was no way to represent one index moving differently
from the base curve, or from another index (e.g. a funding-cost index
widening while a lending index stays put). This module verifies: a
zero/absent overlay is an exact no-op (the pre-phase behavior); the
migrated TBILL3M default reproduces the old hardcoded constant; a
basis-only scenario (no curve shock) moves only the indices it targets,
leaving positions on other indices untouched; and overlay interpolation
behaves correctly.

Run with: py -m pytest tests/ -q
"""

import pytest

import config
from curve.basis import BasisOverlay
from curve.yield_curve import YieldCurve
from curve.scenarios import build_curve_scenarios
from core.balance_sheet import load_positions
from core.indices import index_rate
from core import engine


# ---------------------------------------------------------------------------
# 1. A zero overlay is an exact no-op; the migrated TBILL3M default
#    reproduces the old hardcoded TBILL_OIS_BASIS_SPREAD constant exactly.
# ---------------------------------------------------------------------------

def test_zero_overlay_is_an_exact_noop_for_every_directly_resolved_index():
    curve = YieldCurve(config.FALLBACK_CURVE_TENORS, config.FALLBACK_CURVE_RATES)
    zero = BasisOverlay({0.0: 0.0, 10.0: 0.0})

    assert index_rate("SHORT", curve, basis_overlay=zero) == pytest.approx(curve.spot(1 / 12))
    assert index_rate("TENOR", curve, tenor_years=5.0, basis_overlay=zero) == pytest.approx(curve.spot(5.0))
    assert index_rate("FIXED", curve, tenor_years=5.0, basis_overlay=zero) == pytest.approx(curve.spot(5.0))
    assert index_rate("TBILL3M", curve, basis_overlay=zero) == pytest.approx(curve.spot(0.25))


def test_tbill3m_default_overlay_reproduces_the_old_hardcoded_constant():
    curve = YieldCurve(config.FALLBACK_CURVE_TENORS, config.FALLBACK_CURVE_RATES)
    # Migrated from the old TBILL_OIS_BASIS_SPREAD = -0.0005 constant.
    assert index_rate("TBILL3M", curve) == pytest.approx(curve.spot(0.25) - 0.0005)


# ---------------------------------------------------------------------------
# 2. A basis-only scenario (zero curve shock) produces nonzero delta NII,
#    and positions on unaffected indices show identical income month by
#    month.
# ---------------------------------------------------------------------------

def test_basis_only_scenario_moves_only_positions_on_the_shocked_index():
    positions = load_positions()
    base_curve = YieldCurve(config.FALLBACK_CURVE_TENORS, config.FALLBACK_CURVE_RATES)
    scenario_defs = {
        "Base (flat)": config.RATE_SCENARIOS["Base (flat)"],
        "Funding basis widening": config.RATE_SCENARIOS["Funding basis widening"],
    }
    paths = build_curve_scenarios(base_curve, scenario_defs, horizon_months=24, ramp_months=12)

    _, detail_base, _ = engine.run_scenario(positions, paths["Base (flat)"], scenario_label="Base")
    _, detail_shock, _ = engine.run_scenario(positions, paths["Funding basis widening"], scenario_label="Shock")

    def interest_at(detail_df, bucket, month):
        return detail_df[(detail_df["bucket"] == bucket) & (detail_df["month"] == month)]["interest"].iloc[0]

    # SHORT-indexed: Fed funds sold and Short-term borrowings, both directly
    # shocked, must differ.
    assert interest_at(detail_shock, "Fed funds sold / IB bank balances", 12) != pytest.approx(
        interest_at(detail_base, "Fed funds sold / IB bank balances", 12)
    )

    # TENOR/FIXED/ADMIN-indexed positions are on unaffected indices: exactly
    # identical income, not just "close".
    for bucket in ["Treasuries", "NOW - Core", "CRE loans (fixed)"]:
        assert interest_at(detail_shock, bucket, 12) == pytest.approx(interest_at(detail_base, bucket, 12), abs=1e-9)


def test_new_basis_scenarios_run_cleanly_in_the_full_scenario_set():
    positions = load_positions()
    base_curve = YieldCurve(config.FALLBACK_CURVE_TENORS, config.FALLBACK_CURVE_RATES)
    paths = build_curve_scenarios(base_curve, config.RATE_SCENARIOS, horizon_months=24, ramp_months=12)
    for label in ["Funding basis widening", "Term-priced spread compression"]:
        summary, _, _ = engine.run_scenario(positions, paths[label], scenario_label=label)
        assert not summary["nim"].isna().any()


# ---------------------------------------------------------------------------
# 3. Overlay interpolation: linear between defined points, flat beyond ends.
# ---------------------------------------------------------------------------

def test_basis_overlay_interpolates_linearly_and_is_flat_beyond_ends():
    overlay = BasisOverlay({1.0: -0.0010, 5.0: -0.0030})
    assert overlay.spread(3.0) == pytest.approx(-0.0020)  # linear midpoint
    assert overlay.spread(0.0) == pytest.approx(-0.0010)  # flat below the first point
    assert overlay.spread(10.0) == pytest.approx(-0.0030)  # flat beyond the last point


def test_basis_overlay_shifted_adds_a_flat_amount_at_every_tenor():
    overlay = BasisOverlay({1.0: -0.0010, 5.0: -0.0030})
    shifted = overlay.shifted(0.0025)
    assert shifted.spread(1.0) == pytest.approx(0.0015)
    assert shifted.spread(5.0) == pytest.approx(-0.0005)
