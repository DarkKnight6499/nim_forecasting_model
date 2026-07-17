"""
Liquidity Coverage Ratio (LCR): LCR = HQLA / max(net 30-day outflows, 25% of
gross outflows), Basel III (BCBS238) standard factors.

Takes balances explicitly (not Position.balance, which is each position's
day-0 starting value) so it can be evaluated at any month of a dynamic
simulation, not just day 0.

HQLA caps use the standard non-iterative closed form: L2B capped at 15% of
total HQLA, then L2 (L2A + capped L2B) capped at 40% of total HQLA - each
solved algebraically (e.g. L2B_final <= 0.15*(L1+L2A+L2B_final) rearranges
to L2B_final <= (0.15/0.85)*(L1+L2A)), not by iterating to convergence.

Outflows apply to the full balance for administered/variable liabilities (no
contractual maturity, or already short-term); for laddered liabilities, only
the current month's maturing slice (balance / ladder_months) counts, since
Basel's 30-day outflow only covers cash actually due in that window.
Inflows are the standard 50% factor on fully performing loans' scheduled
runoff (fixed_amortizing positions' base CPR), capped at 75% of gross
outflows.

compute_lcr's additional_outflow parameter is a flat dollar add-on to gross
outflows for exposures this model has no position to represent at all
(derivatives, undrawn credit/liquidity commitments, secured wholesale
funding/repo, other contractual/contingent obligations) - see
data_sources/lcr_disclosures.py, used only when calibrating to a bank with a
real disclosed figure. Zero for the synthetic default book and any
--bank-cert run without a disclosure fixture.
"""

import numpy as np

import config


def compute_hqla(positions, balances):
    l1 = l2a = l2b = 0.0
    hqla_breakdown = {}  # position name -> haircut-adjusted amount, pre-portfolio-level caps
    for p in positions:
        if p.side != "asset" or p.hqla_level is None:
            continue
        haircut = config.LCR_HQLA_HAIRCUTS[p.hqla_level]
        adjusted = balances[p.name] * (1 - haircut)
        hqla_breakdown[p.name] = adjusted
        if p.hqla_level == "L1":
            l1 += adjusted
        elif p.hqla_level == "L2A":
            l2a += adjusted
        elif p.hqla_level == "L2B":
            l2b += adjusted

    l2b_cap_ratio = config.LCR_L2B_CAP_OF_HQLA / (1 - config.LCR_L2B_CAP_OF_HQLA)
    l2b_capped = min(l2b, l2b_cap_ratio * (l1 + l2a))

    l2_cap_ratio = config.LCR_L2_CAP_OF_HQLA / (1 - config.LCR_L2_CAP_OF_HQLA)
    l2_total_capped = min(l2a + l2b_capped, l2_cap_ratio * l1)

    return {"l1": l1, "l2a": l2a, "l2b": l2b, "l2b_capped": l2b_capped,
            "l2_total_capped": l2_total_capped, "hqla": l1 + l2_total_capped, "hqla_breakdown": hqla_breakdown}


def compute_outflows(positions, balances):
    total = 0.0
    breakdown = {}
    for p in positions:
        if p.side != "liability" or p.lcr_outflow_category is None:
            continue
        factor = config.LCR_OUTFLOW_FACTORS[p.lcr_outflow_category]
        outflow_base = balances[p.name] / p.ladder_months if p.category_type == "laddered" else balances[p.name]
        weighted = outflow_base * factor
        breakdown[p.name] = weighted
        total += weighted
    return total, breakdown


def compute_inflows(positions, balances):
    total = 0.0
    breakdown = {}
    for p in positions:
        if p.side != "asset" or p.category_type != "fixed_amortizing":
            continue
        monthly_cpr = 1 - (1 - p.cpr_annual) ** (1 / 12) if p.cpr_annual > 0 else 0.0
        inflow = balances[p.name] * monthly_cpr * config.LCR_PERFORMING_LOAN_INFLOW_FACTOR
        breakdown[p.name] = inflow
        total += inflow
    return total, breakdown


def compute_lcr(positions, balances, additional_outflow=0.0, outflow_adjustment_pct=1.0):
    """
    balances: {position.name: balance $} for the month being evaluated.
    outflow_adjustment_pct: the modified-LCR outflow adjustment percentage
    some banks are subject to under the tailoring rules (< 1.0 reduces total
    net outflow by a fixed percentage); 1.0 for the standard, unmodified LCR.
    """
    hqla_detail = compute_hqla(positions, balances)
    gross_outflows, outflow_breakdown = compute_outflows(positions, balances)
    gross_outflows += additional_outflow
    gross_inflows, inflow_breakdown = compute_inflows(positions, balances)

    capped_inflows = min(gross_inflows, config.LCR_INFLOW_CAP_PCT_OF_OUTFLOWS * gross_outflows)
    floor = (1 - config.LCR_INFLOW_CAP_PCT_OF_OUTFLOWS) * gross_outflows
    net_outflows = max(gross_outflows - capped_inflows, floor) * outflow_adjustment_pct

    lcr = hqla_detail["hqla"] / net_outflows if net_outflows > 0 else np.nan

    return {
        **hqla_detail,
        "gross_outflows": gross_outflows,
        "gross_inflows": gross_inflows,
        "net_outflows": net_outflows,
        "lcr": lcr,
        "outflow_breakdown": outflow_breakdown,
        "inflow_breakdown": inflow_breakdown,
    }
