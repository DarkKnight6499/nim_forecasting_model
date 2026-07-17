"""
Acceptance tests for the balance sheet loader and the cohort-based engine's
core invariants: balance_sheet.yaml's totals, the administered (NMD)
behavioral-duration repricing ladder (vs. the old lag_months point-mass
convention), and the balance-sheet identity holding every simulated month.
Run with: py -m pytest tests/ -q
"""

import pytest

import config
from curve import shocks
from curve.yield_curve import YieldCurve
from curve.scenarios import build_curve_scenarios
from core.balance_sheet import load_positions
from core import engine


# ---------------------------------------------------------------------------
# 1. balance_sheet.yaml reproduces the original default balance sheet totals
# ---------------------------------------------------------------------------

def test_balance_sheet_yaml_matches_original_default_bucket_totals():
    positions = load_positions()
    total_assets = sum(p.balance for p in positions if p.side == "asset")
    total_liab = sum(p.balance for p in positions if p.side == "liability")
    # Reference totals from the original config.DEFAULT_BUCKETS (see git history),
    # plus the $80M Cash & central bank reserves L1 HQLA position added for LCR.
    assert total_assets == pytest.approx(4_780_000_000)
    assert total_liab == pytest.approx(3_300_000_000)


# ---------------------------------------------------------------------------
# 2. Repricing gap: administered (CASA) balances no longer point-mass in 0-3M
# ---------------------------------------------------------------------------

def test_administered_positions_no_longer_point_mass_in_gap_report():
    positions = load_positions()
    casa = [p for p in positions if p.category_type == "administered"]
    total_casa = sum(p.balance for p in casa)

    # Isolate just the administered (CASA) positions' contribution to the near-term
    # bands - not the whole book's RSL, which also includes laddered CDs and
    # variable short-term borrowings that legitimately belong in 0-3M.
    near_term_casa = 0.0
    for p in casa:
        schedule, _ = p.repricing_schedule(config.ALM_MAX_MONTHS)
        near_term_casa += schedule[0:3].sum()

    # Every CASA position here has lag_months <= 3, so the old lag_months-point-mass
    # convention would have dumped the ENTIRE CASA balance into the 0-3M bands.
    # Under the behavioral-duration ladder, only a small fraction should land there.
    assert near_term_casa < 0.25 * total_casa


# ---------------------------------------------------------------------------
# 3. Balance sheet identity holds every month from month 1 onward
# ---------------------------------------------------------------------------

def test_balance_sheet_identity_holds_each_month():
    positions = load_positions()
    flat_curve = YieldCurve([1 / 12, 1.0, 10.0], [0.0425, 0.0425, 0.0425])
    paths = build_curve_scenarios(flat_curve, {"Base": shocks.parallel(0)}, horizon_months=24, ramp_months=12)

    summary_df, detail_df, _ = engine.run_scenario(positions, paths["Base"], scenario_label="Base")

    for month in range(1, 24):
        month_detail = detail_df[detail_df["month"] == month]
        assets = month_detail.loc[month_detail["side"] == "asset", "balance"].sum()
        liabs = month_detail.loc[month_detail["side"] == "liability", "balance"].sum()
        equity = summary_df.loc[summary_df["month"] == month, "equity"].iloc[0]
        assert abs(assets - liabs - equity) < 1.0, f"identity violated at month {month}"
