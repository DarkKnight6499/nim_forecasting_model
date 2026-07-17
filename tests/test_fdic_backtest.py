"""
Acceptance tests for the real-actuals back-test (core/fdic_backtest.py,
data_sources/fdic_history.py, data_sources/treasury_curve.py::get_historical_curves)
and its prerequisite, tag-driven FDIC calibration (Position.calibration_category).

Before this phase, core/backtest.py's demo back-test was circular
(sample_actuals.csv is the model's own output with perturbed assumptions),
and FDIC calibration matched positions by name string, which had already
caused one silent miscategorization bug. This module verifies both are
fixed: renaming a position can't change its calibration scale factor, and a
true out-of-sample replay against a real bank's own subsequently reported
financials runs cleanly offline against recorded fixtures.

Run with: py -m pytest tests/ -q
"""

import datetime
import json
from pathlib import Path

import pandas as pd
import pytest

from core import fdic_backtest
from core.balance_sheet import CALIBRATION_CATEGORIES, load_positions, validate
from data_sources import fdic_bank, fdic_history, treasury_curve

FIXTURES = Path(__file__).parent / "fixtures"
PNC_CERT = 6384


class _FakeResponse:
    def __init__(self, payload_json=None, text=None):
        self._payload_json = payload_json
        self.text = text

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload_json


# ---------------------------------------------------------------------------
# 9.1 prerequisite: calibration_category tags, not name-string matching.
# ---------------------------------------------------------------------------

def test_renaming_a_position_leaves_calibration_scale_factors_identical():
    # A cert with no LCR disclosure fixture (see data_sources/lcr_disclosures.py):
    # keeps this test scoped to calibrate_positions_to_bank's loan/securities/
    # deposit scale factors (criticism 6), not the separate, still name-keyed
    # _apply_lcr_disclosure reallocation (a narrower, bank-specific disclosure
    # feature that identifies HQLA/outflow roles the synthetic template's
    # positions play, out of scope for this fix).
    fin = {
        "NAME": "TEST BANK", "REPDTE": "20260331", "ASSET": "5000000000",
        "ERNAST": "4500000000", "INTINC": "50000", "EINTEXP": "15000",
        "LNLSNET": "3000000", "SC": "900000", "DEP": "3200000",
        "NIMY": "3.0", "EQ": "400000",
    }
    positions_a = load_positions()
    positions_b = load_positions()
    for p in positions_b:
        if p.name == "Treasuries":
            p.name = "Government bonds (renamed, tag unchanged)"

    calibrated_a, _, _ = fdic_bank.calibrate_positions_to_bank(positions_a, 999999, fin=fin)
    calibrated_b, _, _ = fdic_bank.calibrate_positions_to_bank(positions_b, 999999, fin=fin)

    for pa, pb in zip(calibrated_a, calibrated_b):
        assert pa.balance == pytest.approx(pb.balance, rel=1e-12)


def test_validate_raises_on_missing_calibration_category():
    positions = load_positions()
    positions[0].calibration_category = None
    with pytest.raises(ValueError, match="calibration_category"):
        validate(positions)


def test_validate_raises_on_unknown_calibration_category():
    positions = load_positions()
    positions[0].calibration_category = "not_a_real_category"
    with pytest.raises(ValueError, match="calibration_category"):
        validate(positions)


def test_every_default_position_has_a_valid_calibration_category():
    positions = load_positions()  # would raise via validate() if not, but check directly too
    for p in positions:
        assert p.calibration_category in CALIBRATION_CATEGORIES


# ---------------------------------------------------------------------------
# 9.2: fdic_history parses a recorded fixture offline, no live network call.
# ---------------------------------------------------------------------------

def test_fdic_history_parses_fixture_into_n_quarters_with_no_nans(monkeypatch):
    payload = json.loads((FIXTURES / "fdic_financials_pnc.json").read_text())
    monkeypatch.setattr(fdic_history.requests, "get", lambda *a, **kw: _FakeResponse(payload_json=payload))

    df = fdic_history.fetch_quarterly_financials(PNC_CERT, num_quarters=8)
    assert len(df) == 8
    assert not df.isna().any().any()
    assert list(df["month"]) == list(range(8))


def test_fdic_history_quarter_only_nii_deannualizes_ytd_fields_correctly(monkeypatch):
    # Ground truth computed by hand from the fixture's raw cumulative YTD figures:
    # Q4 2025 (REPDTE 20251231) own-quarter NII = (Q4 cumulative - Q3 cumulative) II
    # minus the same for IE. Verifies the de-annualization, not just plumbing.
    payload = json.loads((FIXTURES / "fdic_financials_pnc.json").read_text())
    monkeypatch.setattr(fdic_history.requests, "get", lambda *a, **kw: _FakeResponse(payload_json=payload))

    df = fdic_history.fetch_quarterly_financials(PNC_CERT, num_quarters=8)
    q4_2025 = df[df["quarter_end"] == "20251231"].iloc[0]

    raw = {row["data"]["REPDTE"]: row["data"] for row in payload["data"]}
    q4 = raw["20251231"]
    q3 = raw["20250930"]
    expected_nii = (float(q4["INTINC"]) - float(q3["INTINC"])) * 1000 - (float(q4["EINTEXP"]) - float(q3["EINTEXP"])) * 1000

    assert q4_2025["actual_nii"] == pytest.approx(expected_nii, abs=1.0)


# ---------------------------------------------------------------------------
# 9.2: get_historical_curves parses a recorded Treasury CSV fixture offline.
# ---------------------------------------------------------------------------

def test_get_historical_curves_parses_fixture_and_spot_matches_raw_values(monkeypatch):
    csv_2025 = (FIXTURES / "treasury_curve_2025.csv").read_text()
    csv_2026 = (FIXTURES / "treasury_curve_2026.csv").read_text()

    def fake_get(url, timeout=None):
        if "/2025/" in url:
            return _FakeResponse(text=csv_2025)
        if "/2026/" in url:
            return _FakeResponse(text=csv_2026)
        raise AssertionError(f"unexpected year in URL: {url}")

    monkeypatch.setattr(treasury_curve.requests, "get", fake_get)

    curves = treasury_curve.get_historical_curves(datetime.date(2025, 11, 1), datetime.date(2026, 1, 31))
    assert set(curves.keys()) == {"2025-11", "2025-12", "2026-01"}

    # 12/31/2025 row (month-end): "12/31/2025,3.74,3.75,3.67,3.67,3.63,3.59,3.48,3.47,3.55,3.73,3.94,4.18,4.79,4.84"
    dec_curve = curves["2025-12"]
    assert dec_curve.spot(1 / 12) == pytest.approx(0.0374)
    assert dec_curve.spot(0.25) == pytest.approx(0.0367)
    assert dec_curve.spot(10.0) == pytest.approx(0.0418)

    # 01/30/2026 row (month-end within the fixture): "...,3.72,3.73,3.75,3.67,3.69,3.61,3.48,3.52,3.60,3.79,4.01,4.26,4.82,4.87"
    jan_curve = curves["2026-01"]
    assert jan_curve.spot(1 / 12) == pytest.approx(0.0372)
    assert jan_curve.spot(1.0) == pytest.approx(0.0348)


def test_get_historical_curves_interpolation_flat_beyond_ends(monkeypatch):
    csv_2025 = (FIXTURES / "treasury_curve_2025.csv").read_text()
    monkeypatch.setattr(treasury_curve.requests, "get", lambda url, timeout=None: _FakeResponse(text=csv_2025))

    curves = treasury_curve.get_historical_curves(datetime.date(2025, 12, 31), datetime.date(2025, 12, 31))
    curve = curves["2025-12"]
    assert curve.spot(30.0) == curve.spot(10.0)  # flat beyond the longest defined tenor


# ---------------------------------------------------------------------------
# 9.3: quarterly aggregation identity.
# ---------------------------------------------------------------------------

def test_quarterly_aggregation_sums_monthly_nii_to_the_cent():
    df = pd.DataFrame({
        "month": [1, 2, 3, 4, 5, 6],
        "net_interest_income": [100000.12, 100500.34, 101000.56, 102000.78, 102500.90, 103000.11],
        "avg_earning_assets": [5_000_000_000.0] * 6,
    })
    quarterly = fdic_backtest.aggregate_monthly_to_quarterly(df)

    q0_expected = round(100000.12 + 100500.34 + 101000.56, 2)
    q1_expected = round(102000.78 + 102500.90 + 103000.11, 2)
    assert round(quarterly.loc[quarterly["month"] == 0, "net_interest_income"].iloc[0], 2) == q0_expected
    assert round(quarterly.loc[quarterly["month"] == 1, "net_interest_income"].iloc[0], 2) == q1_expected


# ---------------------------------------------------------------------------
# 9.3/9.4: full offline pipeline, attribution identity holds.
# ---------------------------------------------------------------------------

def test_fdic_backtest_run_end_to_end_offline_attribution_sums_exactly(monkeypatch):
    fdic_payload = json.loads((FIXTURES / "fdic_financials_pnc.json").read_text())
    csv_2025 = (FIXTURES / "treasury_curve_2025.csv").read_text()
    csv_2026 = (FIXTURES / "treasury_curve_2026.csv").read_text()

    # fdic_history and treasury_curve both `import requests`, i.e. the same
    # module object: one dispatching fake covers both call sites, since a
    # second monkeypatch.setattr on the same shared module would just
    # clobber the first.
    def fake_get(url, params=None, timeout=None):
        if "banks.data.fdic.gov" in url:
            return _FakeResponse(payload_json=fdic_payload)
        if "/2025/" in url:
            return _FakeResponse(text=csv_2025)
        if "/2026/" in url:
            return _FakeResponse(text=csv_2026)
        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr(fdic_history.requests, "get", fake_get)

    # 1 quarter back from the fixture's latest (20260331) is 20251231: horizon
    # needs Dec 2025 through Mar 2026, all present across the two CSV fixtures.
    backtest_df, snapshot_fin = fdic_backtest.run(load_positions(), PNC_CERT, quarters_ago=1)

    assert snapshot_fin["REPDTE"] == "20251231"
    assert len(backtest_df) == 1
    row = backtest_df.iloc[0]
    assert row["nii_error"] == pytest.approx(
        row["rate_variance"] + row["volume_variance"] + row["residual_unmodellable"], rel=1e-9
    )
    assert row["net_interest_income"] > 0
    assert row["actual_nii"] > 0
