"""
FRED (Federal Reserve Economic Data) integration - public, free API.
Get a key at https://fred.stlouisfed.org/docs/api/api_key.html and either
pass it in or set the FRED_API_KEY environment variable.

Used to anchor the model's benchmark rate (proxy for the index most of the
book reprices off) to the latest real-world observation, and optionally to
pull recent history for context charts.
"""

import os
import requests
import pandas as pd

FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"

# Common reference series:
#   DFF     - Effective Federal Funds Rate (daily)
#   SOFR    - Secured Overnight Financing Rate (daily)
#   MPRIME  - Bank Prime Loan Rate
#   DGS2    - 2-Year Treasury Yield
#   DGS10   - 10-Year Treasury Yield


def _get_api_key(api_key=None):
    return api_key or os.environ.get("FRED_API_KEY")


def get_latest_benchmark_rate(api_key=None, series_id="DFF", fallback=None):
    """Returns (rate_as_decimal, as_of_date). Falls back to `fallback` if no key or request fails."""
    key = _get_api_key(api_key)
    if not key:
        print("[fred_rates] No FRED_API_KEY set - using fallback/config benchmark rate.")
        return fallback, None
    try:
        resp = requests.get(FRED_BASE, params={
            "series_id": series_id,
            "api_key": key,
            "file_type": "json",
            "sort_order": "desc",
            "limit": 1,
        }, timeout=15)
        resp.raise_for_status()
        obs = resp.json()["observations"][0]
        if obs["value"] == ".":
            return fallback, None
        return float(obs["value"]) / 100.0, obs["date"]
    except Exception as e:
        print(f"[fred_rates] fetch failed ({e}); using fallback rate")
        return fallback, None


def get_history(series_id="DFF", months=24, api_key=None):
    """Returns a DataFrame [date, rate] of the last `months` of a FRED series, or None on failure."""
    key = _get_api_key(api_key)
    if not key:
        return None
    try:
        resp = requests.get(FRED_BASE, params={
            "series_id": series_id,
            "api_key": key,
            "file_type": "json",
            "sort_order": "desc",
            "limit": months * 31,  # daily series - overfetch, then resample
        }, timeout=15)
        resp.raise_for_status()
        obs = resp.json()["observations"]
        df = pd.DataFrame(obs)
        df = df[df["value"] != "."]
        df["date"] = pd.to_datetime(df["date"])
        df["rate"] = df["value"].astype(float) / 100.0
        df = df.sort_values("date")[["date", "rate"]]
        monthly = df.set_index("date")["rate"].resample("MS").last().dropna()
        return monthly.tail(months).reset_index()
    except Exception as e:
        print(f"[fred_rates] history fetch failed ({e})")
        return None
