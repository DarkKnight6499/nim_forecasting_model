"""
FDIC BankFind Suite API integration - public, no API key required.
Docs: https://banks.data.fdic.gov/docs/

Lets you pull a real bank's latest Call Report totals (assets, loans,
securities, deposits, interest income/expense) and use them to calibrate
the synthetic balance sheet's *shape* (relative mix and yields) to match
that institution's actual scale and pricing.

Caveat: the FDIC Call Report has much more granular category-level fields
(C&I vs CRE vs residential loan splits, deposit-by-type, etc. - see the
UBPR/SDI data dictionary) than what's pulled here. This module recalibrates
at the aggregate level (total loans, total securities, total deposits, blended
yields) and rescales the existing bucket mix proportionally. For a
production-grade build, extend `FIELDS` with the specific Call Report
schedule fields you need and map them 1:1 to buckets instead of rescaling.
"""

import copy
import requests

FDIC_BASE = "https://banks.data.fdic.gov/api"

FIELDS = [
    "REPDTE", "NAME", "ASSET", "ERNAST", "INTINC", "EINTEXP",
    "LNLSNET", "SC", "DEP", "NIMY", "EQ",
]


def find_bank(name_query, limit=10):
    """Search FDIC institutions by name. Returns list of dicts with NAME, CERT, CITY, STALP, ASSET.

    The API's Elasticsearch-backed `filters` param does phrase matching literally
    (an exact NAME: "..." match almost never hits), so we AND together a
    wildcard clause per word instead - this is what actually returns matches.
    """
    words = name_query.split()
    filter_clause = " AND ".join(f"NAME:*{w}*" for w in words)
    resp = requests.get(f"{FDIC_BASE}/institutions", params={
        "filters": filter_clause,
        "fields": "NAME,CERT,CITY,STALP,ASSET,ACTIVE",
        "limit": limit,
        "format": "json",
    }, timeout=15)
    resp.raise_for_status()
    return [row["data"] for row in resp.json().get("data", [])]


def fetch_latest_financials(cert_id):
    """Latest quarterly Call Report financials for a bank, by FDIC certificate number."""
    resp = requests.get(f"{FDIC_BASE}/financials", params={
        "filters": f"CERT:{cert_id}",
        "fields": ",".join(FIELDS),
        "sort_by": "REPDTE",
        "sort_order": "DESC",
        "limit": 1,
        "format": "json",
    }, timeout=15)
    resp.raise_for_status()
    rows = resp.json().get("data", [])
    if not rows:
        raise ValueError(f"No FDIC financials found for CERT={cert_id}")
    return rows[0]["data"]


def calibrate_buckets_to_bank(buckets, cert_id):
    """
    Rescales balances of `buckets` (list of config.Bucket) to a real bank's
    actual latest Call Report totals, and nudges blended yields/costs toward
    what that bank actually reports. Returns (new_bucket_list, total_equity_dollars);
    does not mutate input. total_equity_dollars is None if not reported.
    """
    fin = fetch_latest_financials(cert_id)
    print(f"[fdic_bank] Calibrating to {fin.get('NAME')} (CERT {cert_id}), as of {fin.get('REPDTE')}")

    real_loans = float(fin.get("LNLSNET") or 0)
    real_sec = float(fin.get("SC") or 0)
    real_dep = float(fin.get("DEP") or 0)
    real_asset = float(fin.get("ASSET") or 0)
    # INTINC/EINTEXP in this dataset are cumulative year-to-date, in $thousands;
    # quarter-end REPDTE month tells us how many quarters to de-annualize by.
    period_month = int(str(fin.get("REPDTE"))[4:6]) if fin.get("REPDTE") else 12
    quarters_ytd = max(1, round(period_month / 3))
    ann_factor = 4 / quarters_ytd
    real_int_inc = float(fin.get("INTINC") or 0) * ann_factor * 1000
    real_int_exp = float(fin.get("EINTEXP") or 0) * ann_factor * 1000
    real_loans_d = real_loans * 1000
    real_sec_d = real_sec * 1000
    real_dep_d = real_dep * 1000
    real_asset_d = real_asset * 1000

    out = copy.deepcopy(buckets)

    loan_types = {"C&I loans (variable)", "CRE loans (fixed)", "Residential mortgage", "Consumer / other loans"}
    synth_loans = sum(b.balance for b in out if b.name in loan_types)
    synth_sec = sum(b.balance for b in out if "securities" in b.name.lower())
    synth_dep = sum(b.balance for b in out if b.side == "liability" and "borrowing" not in b.name.lower() and "debt" not in b.name.lower())
    synth_asset = sum(b.balance for b in out if b.side == "asset")

    loan_scale = (real_loans_d / synth_loans) if (real_loans_d and synth_loans) else 1.0
    sec_scale = (real_sec_d / synth_sec) if (real_sec_d and synth_sec) else 1.0
    dep_scale = (real_dep_d / synth_dep) if (real_dep_d and synth_dep) else 1.0
    other_asset_scale = (real_asset_d / synth_asset) if (real_asset_d and synth_asset) else loan_scale

    real_loan_yield = (real_int_inc / real_loans_d) if real_loans_d else None
    real_cost_of_funds = (real_int_exp / real_dep_d) if real_dep_d else None

    for b in out:
        if b.name in loan_types:
            b.balance *= loan_scale
            if real_loan_yield:
                b.rate = max(0.0, b.rate * 0.4 + real_loan_yield * 0.6)  # blend synthetic shape with real yield
        elif "securities" in b.name.lower():
            b.balance *= sec_scale
        elif b.side == "liability" and ("borrowing" not in b.name.lower() and "debt" not in b.name.lower()):
            b.balance *= dep_scale
            if real_cost_of_funds:
                b.rate = max(0.0, b.rate * 0.4 + real_cost_of_funds * 0.6)
        elif b.side == "asset":
            b.balance *= other_asset_scale

    reported_nim = float(fin["NIMY"]) / 100 if fin.get("NIMY") not in (None, "") else None
    if reported_nim:
        print(f"[fdic_bank] Bank's own latest reported NIM: {reported_nim:.2%} (sanity-check your model output against this)")

    total_equity = float(fin["EQ"]) * 1000 if fin.get("EQ") not in (None, "") else None
    return out, total_equity
