"""
Acceptance tests for the interactive HTML dashboard (reporting/dashboard.py):
self-containment (no external requests), the embedded JSON's fidelity to the
run's own DataFrames, and file size.
Run with: py -m pytest tests/ -q
"""

import json
import re

import pytest

import pipeline
from reporting import dashboard


@pytest.fixture(scope="module")
def run_results():
    return pipeline.run(months=24)


@pytest.fixture(scope="module")
def dashboard_html(run_results, tmp_path_factory):
    out_path = tmp_path_factory.mktemp("dashboard") / "dashboard.html"
    dashboard.write_dashboard(run_results, out_path)
    return out_path.read_text(encoding="utf-8")


def _embedded_data(html):
    match = re.search(r"const DATA = (.*);\n", html)
    return json.loads(match.group(1))


# ---------------------------------------------------------------------------
# 1. Self-containment: no external requests of any kind.
# ---------------------------------------------------------------------------

def test_dashboard_is_fully_self_contained(dashboard_html):
    assert "http://" not in dashboard_html
    assert "https://" not in dashboard_html
    assert "<script src=" not in dashboard_html


# ---------------------------------------------------------------------------
# 2. Embedded JSON parses; per-scenario month-0 and final-month NIM match the
#    summary DataFrame to 1e-9.
# ---------------------------------------------------------------------------

def test_embedded_nim_matches_summary_dataframe(run_results, dashboard_html):
    data = _embedded_data(dashboard_html)
    end_month = run_results.months - 1
    for scenario in data["scenarios"]:
        rows = {row["month"]: row["nim"] for row in data["nim_by_scenario"][scenario]}
        summary = run_results.combined_summary[run_results.combined_summary["scenario"] == scenario]
        expected_month0 = summary.loc[summary["month"] == 0, "nim"].iloc[0]
        expected_final = summary.loc[summary["month"] == end_month, "nim"].iloc[0]
        assert rows[0] == pytest.approx(expected_month0, abs=1e-9)
        assert rows[end_month] == pytest.approx(expected_final, abs=1e-9)


# ---------------------------------------------------------------------------
# 3. Every scenario label appears; LCR/NSFR/CET1/both EVE flavors present.
# ---------------------------------------------------------------------------

def test_every_scenario_and_report_key_present(run_results, dashboard_html):
    data = _embedded_data(dashboard_html)
    for scenario in run_results.combined_summary["scenario"].unique():
        assert scenario in data["scenarios"]
        assert scenario in data["nim_by_scenario"]
        assert scenario in data["lcr_by_scenario"]
        assert scenario in data["nsfr_by_scenario"]

    assert len(data["cet1"]) == len(run_results.capital_df)
    assert len(data["eve_linear"]) == len(run_results.eve_df)
    assert len(data["eve_full_reval"]) == len(run_results.full_reval_eve_df)
    assert len(data["joint_view"]) == len(run_results.joint_view_df)
    assert len(data["ftp"]) == len(run_results.ftp_monthly_df)
    assert len(data["mtm"]) == len(run_results.mtm_summary_df)


def test_backtest_key_is_none_when_no_backtest_ran(run_results, dashboard_html):
    data = _embedded_data(dashboard_html)
    assert data["backtest"] is None


# ---------------------------------------------------------------------------
# 4. File size under 2MB on the default synthetic run.
# ---------------------------------------------------------------------------

def test_dashboard_file_size_under_2mb(dashboard_html):
    assert len(dashboard_html.encode("utf-8")) < 2 * 1024 * 1024
