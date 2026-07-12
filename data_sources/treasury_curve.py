"""
US Treasury daily par yield curve - public, no API key required.
Docs/data: https://home.treasury.gov/resource-center/data-chart-center/interest-rates

Provides the base term structure the model's curve scenarios shock. Falls
back to an illustrative curve shape (config.FALLBACK_CURVE_TENORS/RATES) if
the request fails, anchored so its short end matches config.STARTING_BENCHMARK_RATE
or a FRED-sourced anchor.
"""

import datetime
import requests

from curve.yield_curve import YieldCurve

TREASURY_CSV_URL = (
    "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
    "daily-treasury-rates.csv/{year}/all?type=daily_treasury_yield_curve"
    "&field_tdr_date_value={year}&page&_format=csv"
)

# Maps Treasury CSV column names to tenor in years (subset aligned to curve.STANDARD_TENORS).
COLUMN_TENORS = {
    "1 Mo": 1 / 12, "3 Mo": 0.25, "6 Mo": 0.5, "1 Yr": 1.0,
    "2 Yr": 2.0, "3 Yr": 3.0, "5 Yr": 5.0, "10 Yr": 10.0,
}


def fetch_latest_curve(year: int = None) -> YieldCurve:
    """Latest available daily par yield curve. Raises on failure (caller should fall back)."""
    year = year or datetime.date.today().year
    resp = requests.get(TREASURY_CSV_URL.format(year=year), timeout=15)
    resp.raise_for_status()
    lines = resp.text.strip().splitlines()
    header = [h.strip().strip('"') for h in lines[0].split(",")]
    latest = [v.strip().strip('"') for v in lines[1].split(",")]  # most recent date is first row
    row = dict(zip(header, latest))

    tenors, rates = [], []
    for col, tenor in COLUMN_TENORS.items():
        if col in row and row[col]:
            tenors.append(tenor)
            rates.append(float(row[col]) / 100.0)
    if not tenors:
        raise ValueError("No usable tenor columns in Treasury CSV response")
    return YieldCurve(tenors, rates)


def get_base_curve(short_rate_anchor: float, fallback_tenors, fallback_rates) -> tuple:
    """
    Returns (YieldCurve, source_label). Tries the live Treasury curve first; on any
    failure, uses the illustrative fallback shape re-anchored so its short end matches
    `short_rate_anchor` (e.g. the FRED Fed Funds rate, or config.STARTING_BENCHMARK_RATE).
    """
    try:
        curve = fetch_latest_curve()
        return curve, "Treasury daily par yield curve"
    except Exception as e:
        print(f"[treasury_curve] fetch failed ({e}); using illustrative fallback curve shape")
        shift = short_rate_anchor - fallback_rates[0]
        shifted_rates = [max(0.0, r + shift) for r in fallback_rates]
        return YieldCurve(fallback_tenors, shifted_rates), "fallback illustrative curve"
