"""
Deposit price elasticity: how a product's balance growth responds to how
competitive its own rate is against the market. See core/products/administered.py
for where volume_response feeds into the simulation.
"""


def volume_response(offered_rate: float, market_rate: float, elasticity: float) -> float:
    """Growth multiplier: 1 + elasticity * (offered_rate - market_rate)."""
    return 1 + elasticity * (offered_rate - market_rate)


def breakeven_rate_uplift(position, curve, target_volume_uplift: float) -> float:
    """
    Solves for the rate (position.rate plus some uplift) at which total
    funding cost with the uplift equals total funding cost without it.

    "With uplift": the position's existing balance, grown by the extra
    volume the uplift attracts (core.elasticity.volume_response), funded
    entirely at the new, higher rate.
    "Without": the existing balance at today's rate, plus that same extra
    volume funded via the wholesale/market alternative (curve's overnight
    spot) instead of via this deposit.

    Where these are equal is the rate uplift at which paying up for deposits
    stops being cheaper than just going to the wholesale market for the
    incremental funding - the marginal cost of the rate rise on the back
    book exactly offsets the marginal savings from the extra volume it
    attracts. target_volume_uplift (a fractional balance growth target, e.g.
    0.05 for 5%) is used only to check the breakeven point is on the same
    side of zero as the volume growth actually being targeted - raises if
    not, since that means no uplift in the intended direction can pay for
    itself given this position's elasticity.

    Raises ValueError if position.pricing_elasticity is 0 (no volume
    response at all, so no uplift can ever pay for itself), or if the
    breakeven point doesn't move volume in the direction target_volume_uplift asks for.
    """
    if position.pricing_elasticity == 0:
        raise ValueError("breakeven_rate_uplift is undefined when pricing_elasticity is 0")

    market_rate = curve.spot(1 / 12)

    def total_cost_gap(delta):
        extra_volume = position.balance * position.pricing_elasticity * delta
        new_balance = position.balance + extra_volume
        cost_with_uplift = new_balance * (position.rate + delta)
        cost_without = position.balance * position.rate + extra_volume * market_rate
        return cost_with_uplift - cost_without

    # Closed form: total_cost_gap(delta) = balance*delta*(1 - elasticity*(market_rate-rate-delta)),
    # so the non-trivial root (excluding the always-true delta=0) is:
    delta = market_rate - position.rate - 1.0 / position.pricing_elasticity
    assert abs(total_cost_gap(delta)) < 1e-6 * max(1.0, position.balance), \
        "breakeven_rate_uplift's closed form failed its own self-check"

    if target_volume_uplift != 0 and (delta * target_volume_uplift) < 0:
        raise ValueError(
            f"breakeven uplift {delta:.4%} moves volume the opposite way from "
            f"target_volume_uplift={target_volume_uplift:.2%}; no cost-neutral rate rise achieves that target here"
        )
    return position.rate + delta
