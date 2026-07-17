"""
FDIC BankFind history: trailing quarterly financials for the real-actuals
back-test (core/fdic_backtest.py). Same /financials endpoint, field set, and
API family as data_sources/fdic_bank.py (reuses fdic_bank.FIELDS, since the
historical snapshot this module fetches is fed straight into
fdic_bank.calibrate_positions_to_bank, which needs the balance-sheet
composition fields (LNLSNET, SC, DEP, EQ) as well as the income-statement
ones), queried across multiple quarters instead of just the latest one.

Field codes used (FDIC Call Report fields):
  REPDTE  - quarter-end report date, YYYYMMDD.
  INTINC  - total interest and dividend income, $ thousands, CUMULATIVE
            year-to-date (resets each Q1; see _quarter_only_interest below).
  EINTEXP - total interest expense, $ thousands, cumulative year-to-date,
            same convention as INTINC.
  ERNAST  - average earning assets for the quarter, $ thousands (already a
            quarterly average, not a cumulative figure).
  NIMY    - net interest margin, annualized, percent, as reported for that
            quarter (not cumulative).
"""

import requests

from data_sources.fdic_bank import FIELDS

FDIC_BASE = "https://banks.data.fdic.gov/api"


def _fetch_raw(cert_id, limit):
    """Raw FDIC financials rows for `cert_id`, most recent REPDTE first."""
    resp = requests.get(f"{FDIC_BASE}/financials", params={
        "filters": f"CERT:{cert_id}",
        "fields": ",".join(FIELDS),
        "sort_by": "REPDTE",
        "sort_order": "DESC",
        "limit": limit,
        "format": "json",
    }, timeout=15)
    resp.raise_for_status()
    rows = [row["data"] for row in resp.json().get("data", [])]
    if not rows:
        raise ValueError(f"No FDIC financials found for CERT={cert_id}")
    return rows


def _quarter_only_interest(rows_desc):
    """
    rows_desc: raw FDIC rows, most recent REPDTE first (as returned by
    _fetch_raw). Returns a parallel list of (quarter_interest_income,
    quarter_interest_expense) in dollars, de-annualizing INTINC/EINTEXP's
    year-to-date convention: a Q1 row (or the oldest row available, whose
    prior quarter we don't have) is used as-is; every later quarter is that
    quarter's own cumulative total minus the prior quarter's (the next row
    in this descending list, since rows_desc is one quarter apart).
    """
    out = []
    for i, row in enumerate(rows_desc):
        repdte = str(row["REPDTE"])
        month = repdte[4:6]
        intinc = float(row.get("INTINC") or 0) * 1000
        eintexp = float(row.get("EINTEXP") or 0) * 1000
        if month == "03" or i + 1 >= len(rows_desc):
            out.append((intinc, eintexp))
            continue
        prev = rows_desc[i + 1]
        if str(prev["REPDTE"])[:4] != repdte[:4]:
            out.append((intinc, eintexp))
        else:
            prev_intinc = float(prev.get("INTINC") or 0) * 1000
            prev_eintexp = float(prev.get("EINTEXP") or 0) * 1000
            out.append((intinc - prev_intinc, eintexp - prev_eintexp))
    return out


def fetch_snapshot(cert_id, quarters_ago):
    """
    The raw FDIC financials row (same shape as fdic_bank.fetch_latest_financials's
    return) for the quarter `quarters_ago` quarters before the latest available,
    e.g. quarters_ago=1 is the second-most-recent quarter.
    """
    rows = _fetch_raw(cert_id, limit=quarters_ago + 2)
    if quarters_ago >= len(rows):
        raise ValueError(f"Only {len(rows)} quarters of history available for CERT={cert_id}, "
                          f"cannot go back {quarters_ago} quarters")
    return rows[quarters_ago]


def fetch_quarterly_financials(cert_id, num_quarters=8):
    """
    The `num_quarters` most recent quarters of realized financials: quarter
    index 0 is the oldest of that set (num_quarters quarters ago), quarter
    index num_quarters - 1 is the latest available quarter. Pairs with
    fetch_snapshot(cert_id, num_quarters): that snapshot is exactly one
    quarter older than this DataFrame's quarter 0, so this is "what actually
    happened" over the num_quarters quarters following the as-of snapshot.

    Returns a DataFrame: quarter_end, actual_nii, actual_avg_earning_assets,
    actual_nim, month (the quarter index above, named `month` so it merges
    directly with core.backtest.compute_backtest and
    core.fdic_backtest.aggregate_monthly_to_quarterly's output).
    """
    import pandas as pd

    rows = _fetch_raw(cert_id, limit=num_quarters + 2)
    interest = _quarter_only_interest(rows)
    records = []
    for i in range(num_quarters):
        row = rows[i]
        q_ii, q_ie = interest[i]
        ernast = float(row.get("ERNAST") or 0) * 1000
        nimy = row.get("NIMY")
        actual_nim = float(nimy) / 100 if nimy not in (None, "") else ((q_ii - q_ie) * 4 / ernast if ernast else None)
        records.append({
            "quarter_end": str(row["REPDTE"]),
            "actual_nii": q_ii - q_ie,
            "actual_avg_earning_assets": ernast,
            "actual_nim": actual_nim,
        })
    records.reverse()  # oldest first: quarter index 0 = the first quarter after the as-of snapshot
    df = pd.DataFrame(records)
    df["month"] = range(len(df))
    return df
