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
yields) and rescales the existing position mix proportionally. For a
production-grade build, extend `FIELDS` with the specific Call Report
schedule fields you need and map them 1:1 to positions instead of rescaling.
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


def calibrate_positions_to_bank(positions, cert_id):
    """
    Rescales balances of `positions` (list of core.position.Position) to a real
    bank's actual latest Call Report totals, and rescales rates so day-0
    modeled income/expense match what that bank actually reports. Returns
    (new_position_list, total_equity_dollars); does not mutate input.
    total_equity_dollars is None if not reported.
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

    out = copy.deepcopy(positions)

    loan_types = {"C&I loans (variable)", "CRE loans (fixed)", "Residential mortgage", "Consumer / other loans"}
    synth_loans = sum(b.balance for b in out if b.name in loan_types)
    synth_sec = sum(b.balance for b in out if "securities" in b.name.lower())
    synth_dep = sum(b.balance for b in out if b.side == "liability" and "borrowing" not in b.name.lower() and "debt" not in b.name.lower())
    synth_asset = sum(b.balance for b in out if b.side == "asset")

    loan_scale = (real_loans_d / synth_loans) if (real_loans_d and synth_loans) else 1.0
    sec_scale = (real_sec_d / synth_sec) if (real_sec_d and synth_sec) else 1.0
    dep_scale = (real_dep_d / synth_dep) if (real_dep_d and synth_dep) else 1.0
    other_asset_scale = (real_asset_d / synth_asset) if (real_asset_d and synth_asset) else loan_scale

    for b in out:
        if b.name in loan_types:
            b.balance *= loan_scale
        elif "securities" in b.name.lower():
            b.balance *= sec_scale
        elif b.side == "liability" and ("borrowing" not in b.name.lower() and "debt" not in b.name.lower()):
            b.balance *= dep_scale
        elif b.side == "asset":
            b.balance *= other_asset_scale

    # FDIC's public summary financials only give aggregate INTINC/EINTEXP, not a
    # per-loan-type yield or per-deposit-type cost - so there's no real data to blend
    # into individual bucket rates (an earlier version tried to anyway, by dividing
    # total interest income by loan balances alone, and total interest expense by
    # deposit balances alone - overstating both, since INTINC also includes securities/
    # fed-funds income and EINTEXP also includes borrowings expense). The only real,
    # calibratable signal available is the aggregate: total day-0 income/expense should
    # match what the bank actually reported, since every scenario/gap/EVE/EaR number
    # downstream is a *delta* off this starting point. Rescale all asset rates (and
    # separately all liability rates) by one multiplicative factor each, preserving the
    # relative pricing shape from config.py while forcing the aggregate day-0 NIM to match.
    implied_ii = sum(b.balance * b.rate for b in out if b.side == "asset")
    implied_ie = sum(b.balance * b.rate for b in out if b.side == "liability")
    if real_int_inc and implied_ii:
        income_scale = real_int_inc / implied_ii
        for b in out:
            if b.side == "asset":
                b.rate *= income_scale
    if real_int_exp and implied_ie:
        expense_scale = real_int_exp / implied_ie
        for b in out:
            if b.side == "liability":
                b.rate *= expense_scale

    reported_nim = float(fin["NIMY"]) / 100 if fin.get("NIMY") not in (None, "") else None
    if reported_nim:
        print(f"[fdic_bank] Bank's own latest reported NIM: {reported_nim:.2%} (sanity-check your model output against this)")

    total_equity = float(fin["EQ"]) * 1000 if fin.get("EQ") not in (None, "") else None
    return out, total_equity
