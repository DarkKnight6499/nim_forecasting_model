"""
Net Stable Funding Ratio (NSFR): NSFR = ASF / RSF, Basel III (BCBS295)
standard factors, simplified to the categories this balance sheet actually
uses and reusing the tags positions already carry (hqla_level,
lcr_outflow_category, calibration_category) plus the ladder/maturity info
category_type and ladder_months already expose - no new per-position tags
beyond Position.rsf_factor_override (an explicit escape hatch for asset
classes, like residential mortgages, that get a preferential Basel RSF
treatment distinct from the general performing-loan rate).

Basel's ASF/RSF tables are keyed on *residual contractual maturity* buckets
(<6m, 6m-1y, >=1y), not this model's coarser "administered / laddered" split.
For laddered positions (a uniform maturity ladder: 1/ladder_months of the
balance matures each month, so residual maturities run 1..ladder_months
months), the ASF/RSF factor is a blend: the fraction of the ladder with
residual maturity >= 12 months gets the >=1y factor, the rest gets the
category's <1y factor. Administered (NMD) positions have no contractual
maturity at all, so Basel treats them by behavioral stability category
directly (stable/less-stable retail ASF factors) regardless of any ladder
concept; a wholesale-tagged administered position (e.g. an operational
deposit) is treated at the shortest, most conservative wholesale bucket for
the same reason. Variable-rate positions reset monthly but have no
amortization schedule in this model (e.g. C&I revolving credit lines), so
they're treated at the shortest bucket on both sides of the balance sheet.

ASF (liabilities + capital): capital 100%; stable retail 95%; less-stable
retail 90%; term deposits blend 90%/100% by ladder residual maturity;
wholesale funding blends 50%/100% by ladder residual maturity (administered
wholesale deposits flat 50%, no ladder to blend against).

RSF (assets): cash 0%; HQLA L1 5%, L2A 15%, L2B 50% (calibration_category
"cash" wins over hqla_level, so central bank reserves get 0% rather than the
generic L1 5%, matching Basel's distinct treatment of physical cash/reserves
vs. other Level 1 HQLA); performing loans blend 50%/85% by average life
(fixed_amortizing: 1/cpr_annual, capped at 30y) or a flat 50% for variable-
rate loans (no amortization schedule to derive an average life from);
rsf_factor_override, when set, wins over all of the above (residential
mortgages: 65%, a preferential rate vs. the general 85% performing-loan
rate). Anything else defaults to the conservative 100% (NSFR_RSF_OTHER).
"""

import numpy as np

import config


def _fraction_over_one_year(ladder_months: int) -> float:
    return max(0.0, ladder_months - 12) / ladder_months if ladder_months > 0 else 0.0


def _asf_factor(p) -> float:
    if p.category_type == "administered":
        if p.lcr_outflow_category == "stable_retail":
            return config.NSFR_ASF_STABLE_RETAIL
        if p.lcr_outflow_category == "less_stable_retail":
            return config.NSFR_ASF_LESS_STABLE_RETAIL
        if p.lcr_outflow_category in ("wholesale_operational", "wholesale_non_operational"):
            return config.NSFR_ASF_WHOLESALE_UNDER_1Y
        return 0.0

    if p.category_type == "variable":
        return config.NSFR_ASF_WHOLESALE_UNDER_1Y

    if p.category_type == "laddered":
        frac_over_1y = _fraction_over_one_year(p.ladder_months)
        under_1y_factor = (config.NSFR_ASF_TERM_DEPOSIT_UNDER_1Y if p.lcr_outflow_category == "term_deposit"
                            else config.NSFR_ASF_WHOLESALE_UNDER_1Y)
        return frac_over_1y * config.NSFR_ASF_OVER_1Y + (1 - frac_over_1y) * under_1y_factor

    return 0.0


def _rsf_factor(p) -> float:
    if p.rsf_factor_override is not None:
        return p.rsf_factor_override
    if p.calibration_category == "cash":
        return config.NSFR_RSF_CASH
    if p.hqla_level == "L1":
        return config.NSFR_RSF_L1
    if p.hqla_level == "L2A":
        return config.NSFR_RSF_L2A
    if p.hqla_level == "L2B":
        return config.NSFR_RSF_L2B
    if p.calibration_category == "loans":
        if p.category_type == "variable":
            return config.NSFR_RSF_LOAN_UNDER_1Y
        avg_life_years = (1 / p.cpr_annual) if p.cpr_annual > 0 else 30.0
        avg_life_years = min(avg_life_years, 30.0)
        return config.NSFR_RSF_LOAN_UNDER_1Y if avg_life_years < 1.0 else config.NSFR_RSF_LOAN_OVER_1Y
    return config.NSFR_RSF_OTHER


def compute_nsfr(positions, balances, equity):
    """
    balances: {position.name: balance $} for the month being evaluated.
    equity: total equity capital that month (100% ASF).
    """
    asf = equity
    asf_breakdown = {"Capital (equity)": equity}
    for p in positions:
        if p.side != "liability":
            continue
        contribution = balances[p.name] * _asf_factor(p)
        asf += contribution
        asf_breakdown[p.name] = contribution

    rsf = 0.0
    rsf_breakdown = {}
    for p in positions:
        if p.side != "asset":
            continue
        contribution = balances[p.name] * _rsf_factor(p)
        rsf += contribution
        rsf_breakdown[p.name] = contribution

    nsfr = asf / rsf if rsf > 0 else np.nan

    return {"asf": asf, "rsf": rsf, "nsfr": nsfr, "asf_breakdown": asf_breakdown, "rsf_breakdown": rsf_breakdown}
