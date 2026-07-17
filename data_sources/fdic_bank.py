"""
FDIC BankFind Suite API integration - public, no API key required.
Docs: https://banks.data.fdic.gov/docs/

Pulls a real bank's latest Call Report totals (assets, loans, securities,
deposits, interest income/expense) and rescales the synthetic balance
sheet's position mix and rates to match, at the aggregate level (FDIC's
public summary financials don't break out per-category yields).

If a real LCR Pillar 3 disclosure fixture exists for the calibrated bank
(data_sources/lcr_disclosures.py), the securities and deposit envelopes are
further reallocated to match that bank's own reported HQLA composition and
deposit outflow-stability mix, instead of carrying over the synthetic
template's assumed proportions - see _apply_lcr_disclosure below.
"""

import copy
import requests

from data_sources import lcr_disclosures

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


def _apply_lcr_disclosure(out, disclosure, real_sec_d, real_dep_d):
    """
    Reallocates the (already correctly-totaled) securities and deposit
    envelopes to match a real bank's disclosed HQLA composition and deposit
    outflow-stability mix, preserving each envelope's total dollar amount
    (real_sec_d, real_dep_d) - only how it's split across positions changes.
    Returns the disclosure's additional_outflow_weighted scalar (off-balance-
    sheet exposures this model has no position to represent at all).
    """
    by_name = {p.name: p for p in out}

    # HQLA: eligible cash split between Fed funds sold and central bank
    # reserves preserving their pre-existing relative proportions; Treasuries
    # and Agency MBS set to the bank's own disclosed face values. Munis
    # absorbs whatever's left of the real securities total; if the bank
    # discloses zero Level 2B (as PNC does), its hqla_level is cleared since
    # that residual isn't LCR-eligible for this bank.
    ff_sold = by_name["Fed funds sold / IB bank balances"]
    cash_reserves = by_name["Cash & central bank reserves"]
    cash_total = ff_sold.balance + cash_reserves.balance
    eligible_cash = disclosure["hqla_eligible_cash"]
    if cash_total > 0:
        ff_sold.balance = eligible_cash * (ff_sold.balance / cash_total)
        cash_reserves.balance = eligible_cash * (cash_reserves.balance / cash_total)

    treasuries = by_name["Treasuries"]
    agency_mbs = by_name["Agency MBS"]
    munis = by_name["Municipal & corporate bonds"]
    treasuries.balance = disclosure["hqla_level1_securities"]
    agency_mbs.balance = disclosure["hqla_level2a_face_value"]
    l2b_face = disclosure["hqla_level2b_face_value"]
    if l2b_face > 0:
        munis.balance = l2b_face + max(0.0, real_sec_d - treasuries.balance - agency_mbs.balance - l2b_face)
    else:
        munis.balance = max(0.0, real_sec_d - treasuries.balance - agency_mbs.balance)
        munis.hqla_level = None

    # Deposits: NOW - Non-Core and Savings & MMDA - Non-Core are already
    # flagged feeds_mclr_deposit_cost (market-sensitive, non-core), so they're
    # repurposed here to carry the bank's disclosed wholesale operational /
    # non-operational deposit mix (brokered folded into non-operational). The
    # remaining retail target is split across NOW - Core, Savings & MMDA -
    # Core and Time deposits (CDs), preserving their existing relative mix.
    retail_amt = disclosure["retail_deposit_outflow"]
    wholesale_op_amt = disclosure["wholesale_operational_outflow"]
    wholesale_nonop_amt = disclosure["wholesale_non_operational_outflow"] + disclosure["brokered_deposit_outflow"]
    disclosed_total = retail_amt + wholesale_op_amt + wholesale_nonop_amt
    scale = (real_dep_d / disclosed_total) if disclosed_total else 1.0

    now_noncore = by_name["NOW - Non-Core"]
    savings_noncore = by_name["Savings & MMDA - Non-Core"]
    now_noncore.balance = wholesale_op_amt * scale
    now_noncore.lcr_outflow_category = "wholesale_operational"
    savings_noncore.balance = wholesale_nonop_amt * scale
    savings_noncore.lcr_outflow_category = "wholesale_non_operational"

    retail_positions = [by_name["NOW - Core"], by_name["Savings & MMDA - Core"], by_name["Time deposits (CDs)"]]
    retail_target = retail_amt * scale
    retail_current_total = sum(p.balance for p in retail_positions)
    if retail_current_total > 0:
        for p in retail_positions:
            p.balance = retail_target * (p.balance / retail_current_total)

    return disclosure["additional_outflow_weighted"]


def calibrate_positions_to_bank(positions, cert_id):
    """
    Rescales `positions` balances and rates to a real bank's latest Call
    Report. Returns (new_position_list, total_equity_dollars, lcr_calibration);
    does not mutate input. total_equity_dollars is None if not reported.
    lcr_calibration is {"additional_outflow": 0.0, "outflow_adjustment_pct": 1.0}
    unless a real LCR disclosure fixture exists for cert_id (see
    data_sources/lcr_disclosures.py), in which case it holds that bank's own
    disclosed values - pass it straight into core.lcr.compute_lcr(**lcr_calibration).
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
    security_types = {"Treasuries", "Agency MBS", "Municipal & corporate bonds"}
    synth_loans = sum(b.balance for b in out if b.name in loan_types)
    synth_sec = sum(b.balance for b in out if b.name in security_types)
    synth_dep = sum(b.balance for b in out if b.side == "liability" and "borrowing" not in b.name.lower() and "debt" not in b.name.lower())
    synth_asset = sum(b.balance for b in out if b.side == "asset")

    loan_scale = (real_loans_d / synth_loans) if (real_loans_d and synth_loans) else 1.0
    sec_scale = (real_sec_d / synth_sec) if (real_sec_d and synth_sec) else 1.0
    dep_scale = (real_dep_d / synth_dep) if (real_dep_d and synth_dep) else 1.0
    other_asset_scale = (real_asset_d / synth_asset) if (real_asset_d and synth_asset) else loan_scale

    for b in out:
        if b.name in loan_types:
            b.balance *= loan_scale
        elif b.name in security_types:
            b.balance *= sec_scale
        elif b.side == "liability" and ("borrowing" not in b.name.lower() and "debt" not in b.name.lower()):
            b.balance *= dep_scale
        elif b.side == "asset":
            b.balance *= other_asset_scale

    lcr_calibration = {"additional_outflow": 0.0, "outflow_adjustment_pct": 1.0}
    disclosure = lcr_disclosures.get_disclosure(cert_id)
    if disclosure:
        lcr_calibration["additional_outflow"] = _apply_lcr_disclosure(out, disclosure, real_sec_d, real_dep_d)
        lcr_calibration["outflow_adjustment_pct"] = disclosure["outflow_adjustment_pct"]
        print(f"[fdic_bank] LCR composition grounded in {disclosure['source']} "
              f"(disclosed LCR: {disclosure['disclosed_lcr']:.0%})")

    # Rescale all asset rates (and separately all liability rates) by one factor each
    # so day-0 modeled interest income/expense match the bank's reported totals.
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
    return out, total_equity, lcr_calibration
