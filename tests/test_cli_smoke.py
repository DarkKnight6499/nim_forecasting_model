"""
End-to-end CLI smoke tests: `python main.py` (synthetic book) and
`python main.py --bank-cert 6384` (real-bank calibration) both still run to
completion, and the PNC-calibrated run's day-0 NIM matches PNC's own latest
reported NIM within 10bps (the day-0 calibration invariant every phase of
this model has to preserve). Consolidated from several near-duplicate
per-phase smoke tests into one file: each is a ~15-20s subprocess spawn, so
duplicating them per feature phase was pure runtime cost with no additional
coverage - one synthetic-book run and one PNC run exercise the entire CLI
path regardless of which internal module changed.
Run with: py -m pytest tests/ -q
"""

import subprocess
import sys


def _run_main(*args):
    return subprocess.run([sys.executable, "main.py", *args], capture_output=True, text=True, timeout=120)


def test_main_runs_end_to_end_synthetic():
    result = _run_main()
    assert result.returncode == 0, result.stderr


def test_main_runs_end_to_end_pnc_and_matches_reported_nim():
    result = _run_main("--bank-cert", "6384")
    assert result.returncode == 0, result.stderr
    output = result.stdout

    reported_line = next(line for line in output.splitlines() if "Bank's own latest reported NIM" in line)
    reported_pct = float(reported_line.split(":")[1].strip().split("%")[0])

    month0_line = next(line for line in output.splitlines() if line.startswith("Month  0"))
    base_token = [tok for tok in month0_line.replace("Base (flat)", "Base(flat)").split() if tok.startswith("Base(flat)")][0]
    model_pct = float(base_token.split("=")[1].rstrip("%"))

    assert abs(model_pct - reported_pct) < 0.10  # within 10bps

    # Broad section coverage: the CLI's output should still cover every major
    # report, regardless of which internal module last changed.
    for section in ("ALM Desk P&L stability", "Net Stable Funding Ratio", "CET1 Ratio", "Liquidity Coverage Ratio"):
        assert section in output
