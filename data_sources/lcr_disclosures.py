"""
Real bank Basel III LCR Pillar 3 disclosure figures, checked in as a fixture.

Unlike the aggregate Call Report totals fdic_bank.py already pulls live from
FDIC BankFind, HQLA composition (Level 1/2A/2B) and deposit outflow-stability
mix are not part of any free structured API: only banks above the LCR
disclosure threshold publish them, quarterly, as PDFs. calibrate_positions_to_bank
uses this fixture (when one exists for the calibrated cert_id) to ground a
--bank-cert run's day-0 LCR in the bank's OWN reported composition, instead of
carrying over the synthetic template's assumed HQLA/deposit-stability mix.

Source, PNC (CERT 6384): "The PNC Financial Services Group, Inc., Liquidity
Coverage Ratio and Net Stable Funding Ratio Disclosures, December 31, 2025",
Table 2, Table 3 and Table 4 (pnc.com/regulatorydisclosures). Figures are the
quarter's average weighted (HQLA) or average unweighted (deposit outflow
categories, face-value basis) amounts, converted from the disclosure's
dollar-millions.
"""

LCR_DISCLOSURES = {
    6384: {
        "as_of": "2025-12-31",
        "source": "PNC LCR/NSFR Disclosures, December 31, 2025, Tables 2-4",
        # HQLA composition, average weighted (post-haircut) amounts.
        "hqla_eligible_cash": 31_276_000_000,
        "hqla_level1_securities": 71_216_000_000,
        "hqla_level2a_face_value": 5_728_000_000,  # disclosure's own unweighted L2A figure
        "hqla_level2b_face_value": 0,
        # Deposit outflow categories, average unweighted (face-value) amounts.
        "retail_deposit_outflow": 251_436_000_000,
        "wholesale_operational_outflow": 96_356_000_000,
        "wholesale_non_operational_outflow": 70_283_000_000,
        "brokered_deposit_outflow": 11_383_000_000,
        # Everything else in Table 2 the model has no position to represent:
        # secured wholesale funding/asset exchange (row 13), derivatives and
        # undrawn credit/liquidity commitments (row 14), other contractual
        # and contingent funding obligations (rows 17-18). Average weighted
        # amount, added as a flat outflow scalar (3,235 + 46,923 + 685 + 671).
        "additional_outflow_weighted": 51_514_000_000,
        # Table 2, row 33: the modified-LCR outflow adjustment percentage
        # applicable to PNC's size/category under the 2019 tailoring rules
        # (a bank subject to the full, unmodified LCR would have 1.00 here).
        # Applied to total net cash outflow after the inflow cap/floor.
        "outflow_adjustment_pct": 0.85,
        "disclosed_lcr": 1.08,
    },
}


def get_disclosure(cert_id):
    return LCR_DISCLOSURES.get(cert_id)
