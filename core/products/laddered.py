"""
Laddered mechanics: single blended state, 1/ladder_months matures each month.
Only renewal_rate of the maturing amount rolls into new production at the
current curve tenor; the rest is a funding outflow (the engine's plug/
cash_sink absorbs it via the balance sheet identity next month, same as any
other balance shortfall). early_withdrawal_annual applies extra runoff to
the not-yet-matured (surviving) balance, on top of scheduled maturities.
growth_rate_annual may be negative (renewed volume shrinks faster than
non-renewal alone); renewed is floored at zero rather than growth being
floored, so a negative growth still reduces the rolled-over amount.
"""

from core.indices import index_rate


def step(p, prev_balance, prev_rate, curve_t, t, basis_overlay=None):
    maturing = prev_balance / p.ladder_months
    growth = prev_balance * (p.growth_rate_annual / 12)
    renewed = max(0.0, maturing * p.renewal_rate + growth)
    new_rate_piece = index_rate(p.index, curve_t, tenor_years=p.origination_tenor_years,
                                 basis_overlay=basis_overlay) + p.spread
    if p.rate_floor is not None:
        new_rate_piece = max(new_rate_piece, p.rate_floor)

    surviving = prev_balance - maturing
    if p.early_withdrawal_annual:
        monthly_early_withdrawal = 1 - (1 - p.early_withdrawal_annual) ** (1 / 12)
        surviving -= surviving * monthly_early_withdrawal

    new_balance = surviving + renewed
    if new_balance <= 0:
        return 0.0, new_rate_piece, renewed, new_rate_piece
    blended_rate = (surviving * prev_rate + renewed * new_rate_piece) / new_balance
    return new_balance, blended_rate, renewed, new_rate_piece
