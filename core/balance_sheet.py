"""
Loads the balance sheet from `balance_sheet.yaml` (contractual/behavioral
assumptions as data) instead of the old hardcoded `config.DEFAULT_BUCKETS`
Python list. This makes swapping in a different bank's book, or recalibrating
one, a data-mapping problem rather than an in-code rescaling hack.
"""

from pathlib import Path

import yaml

from core.position import Position

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "balance_sheet.yaml"

_VALID_CATEGORY_TYPES = {"variable", "administered", "fixed_amortizing", "laddered"}


def load_positions(path=None) -> list:
    path = Path(path) if path else DEFAULT_PATH
    with open(path, "r") as f:
        raw = yaml.safe_load(f)
    positions = [Position(**entry) for entry in raw["positions"]]
    validate(positions)
    return positions


def validate(positions: list) -> None:
    if not positions:
        raise ValueError("Balance sheet has no positions")

    plugs = [p for p in positions if p.plug]
    if len(plugs) != 1:
        raise ValueError(f"Expected exactly one plug position, found {len(plugs)}")

    sinks = [p for p in positions if p.cash_sink]
    if len(sinks) != 1:
        raise ValueError(f"Expected exactly one cash_sink position, found {len(sinks)}")

    for p in positions:
        if p.side not in ("asset", "liability"):
            raise ValueError(f"{p.name}: side must be 'asset' or 'liability', got {p.side!r}")
        if p.category_type not in _VALID_CATEGORY_TYPES:
            raise ValueError(f"{p.name}: unknown category_type {p.category_type!r}")
        if p.balance < 0:
            raise ValueError(f"{p.name}: negative balance {p.balance}")
        if p.category_type == "administered" and p.behavioral_duration_years is None and p.liquidity_decay_annual is not None:
            raise ValueError(f"{p.name}: has liquidity_decay_annual but no behavioral_duration_years")
