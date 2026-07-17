"""
Acceptance tests for grounding a --bank-cert run's day-0 LCR in the bank's
own real Basel III Pillar 3 disclosure (data_sources/lcr_disclosures.py),
instead of carrying over the synthetic template's assumed HQLA composition
and deposit outflow-stability mix.

Before this fix, the model's day-0 LCR for both the default synthetic book
and any --bank-cert run reflected only the synthetic template's HQLA/deposit
assumptions, scaled by aggregate loan/securities/deposit totals - the LCR
itself was never checked against anything real, and for PNC (CERT 6384) it
came out near 430-600%, nowhere close to PNC's own disclosed 108%. This
module verifies the fix lands close to the real, cited figure, and that the
default synthetic book (explicitly out of scope for this fix) is untouched.

Run with: py -m pytest tests/ -q
"""

import pytest

from core.balance_sheet import load_positions
from core import lcr
from data_sources import fdic_bank, lcr_disclosures


PNC_CERT = 6384


# ---------------------------------------------------------------------------
# 1. PNC-calibrated day-0 LCR lands close to PNC's own disclosed figure.
# ---------------------------------------------------------------------------

def test_pnc_calibrated_lcr_matches_disclosed_figure_closely():
    positions = load_positions()
    calibrated, total_equity, lcr_calibration = fdic_bank.calibrate_positions_to_bank(positions, PNC_CERT)
    balances = {p.name: p.balance for p in calibrated}
    result = lcr.compute_lcr(calibrated, balances, **lcr_calibration)

    disclosure = lcr_disclosures.get_disclosure(PNC_CERT)
    assert abs(result["lcr"] - disclosure["disclosed_lcr"]) < 0.05  # within 5 percentage points


def test_pnc_calibrated_hqla_matches_disclosed_composition():
    positions = load_positions()
    calibrated, _, lcr_calibration = fdic_bank.calibrate_positions_to_bank(positions, PNC_CERT)
    balances = {p.name: p.balance for p in calibrated}
    result = lcr.compute_lcr(calibrated, balances, **lcr_calibration)

    disclosure = lcr_disclosures.get_disclosure(PNC_CERT)
    disclosed_hqla = (
        disclosure["hqla_eligible_cash"] + disclosure["hqla_level1_securities"]
        + disclosure["hqla_level2a_face_value"] * 0.85  # L2A haircut, matches disclosure's own weighted figure
    )
    assert result["hqla"] == pytest.approx(disclosed_hqla, rel=1e-6)


# ---------------------------------------------------------------------------
# 2. The default synthetic book is untouched: this fix is scoped to
#    --bank-cert calibration only, per explicit instruction.
# ---------------------------------------------------------------------------

def test_default_synthetic_book_lcr_is_unaffected_by_disclosure_calibration():
    positions = load_positions()
    balances = {p.name: p.balance for p in positions}
    result = lcr.compute_lcr(positions, balances)  # no additional_outflow / adjustment kwargs

    # Regression pin: same value the model produced before this fix existed.
    assert result["lcr"] == pytest.approx(4.289641495275856, rel=1e-6)


def test_compute_lcr_defaults_reproduce_unmodified_behavior():
    # additional_outflow=0.0 and outflow_adjustment_pct=1.0 must be true no-ops.
    positions = load_positions()
    balances = {p.name: p.balance for p in positions}
    default_result = lcr.compute_lcr(positions, balances)
    explicit_result = lcr.compute_lcr(positions, balances, additional_outflow=0.0, outflow_adjustment_pct=1.0)
    assert default_result["lcr"] == pytest.approx(explicit_result["lcr"], rel=1e-12)


# ---------------------------------------------------------------------------
# 3. A bank-cert run without a disclosure fixture falls back to the
#    pre-existing (unmodified) calibration behavior.
# ---------------------------------------------------------------------------

def test_calibration_without_a_disclosure_fixture_returns_neutral_lcr_calibration():
    assert lcr_disclosures.get_disclosure(999999) is None

    class _FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"data": [{"data": {
                "NAME": "TEST BANK", "REPDTE": "20260331", "ASSET": "1000000",
                "ERNAST": "900000", "INTINC": "10000", "EINTEXP": "3000",
                "LNLSNET": "600000", "SC": "200000", "DEP": "800000",
                "NIMY": "3.0", "EQ": "100000",
            }}]}

    positions = load_positions()
    original_fetch = fdic_bank.fetch_latest_financials
    fdic_bank.fetch_latest_financials = lambda cert_id: _FakeResponse().json()["data"][0]["data"]
    try:
        _, _, lcr_calibration = fdic_bank.calibrate_positions_to_bank(positions, 999999)
    finally:
        fdic_bank.fetch_latest_financials = original_fetch

    assert lcr_calibration == {"additional_outflow": 0.0, "outflow_adjustment_pct": 1.0}
