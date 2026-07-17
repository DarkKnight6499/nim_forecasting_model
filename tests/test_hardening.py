"""
Acceptance tests for the orchestration split: main.py used to be a ~300-line
monolith mixing argparse, orchestration, and console printing (criticism 9).
It's now a thin entry point over pipeline.py (all computation, returns one
RunResults dataclass) and reporting/console.py (all structured-report
printing). No behavior change: the regression pin below and the byte-for-byte
CLI output comparison done by hand during this refactor (see commit message)
both confirm the split doesn't move any number.
Run with: py -m pytest tests/ -q
"""

import re
from pathlib import Path

import pytest

import pipeline

REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# 1. pipeline.run returns a RunResults matching pre-split regression values.
# ---------------------------------------------------------------------------

def test_pipeline_run_returns_correct_run_results_regression():
    results = pipeline.run(months=24)
    assert isinstance(results, pipeline.RunResults)

    base = results.combined_summary[results.combined_summary["scenario"] == results.base_label]
    total_nii = base["net_interest_income"].sum()
    # Regression pin: the default synthetic book's 24-month base-scenario NII,
    # from before this split (same call path, same defaults).
    assert total_nii == pytest.approx(371_230_196.03, abs=1.0)


# ---------------------------------------------------------------------------
# 2. main.py stays a thin entry point; no print() calls leak outside
#    reporting/console.py among the pieces that used to be the monolith.
#    (data_sources/* modules print their own fetch-time diagnostics - a
#    separate, pre-existing cross-cutting concern this phase doesn't touch,
#    not part of "the monolith" criticism 9 describes.)
# ---------------------------------------------------------------------------

def test_main_py_is_a_thin_entry_point():
    lines = (REPO_ROOT / "main.py").read_text().splitlines()
    assert len(lines) < 60


@pytest.mark.parametrize("module_path", ["main.py", "pipeline.py", "reporting/charts.py", "reporting/export.py"])
def test_no_print_calls_outside_console_module(module_path):
    source = (REPO_ROOT / module_path).read_text()
    assert not re.search(r"\bprint\s*\(", source), f"{module_path} should not call print() directly"


def test_console_module_covers_every_report_main_used_to_print_inline():
    from reporting import console
    for fn_name in (
        "print_bank_search", "print_nim_by_scenario", "print_sensitivity", "print_gap", "print_liquidity",
        "print_duration_and_eve", "print_full_reval_eve", "print_ear", "print_ftp_pnl", "print_ftp_stability",
        "print_lcr", "print_nsfr", "print_capital", "print_joint_view", "print_mtm", "print_backtest",
        "print_fdic_backtest", "print_ftp_recalibration", "print_output_paths",
    ):
        assert callable(getattr(console, fn_name, None)), f"reporting/console.py missing {fn_name}"
