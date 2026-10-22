"""Baseline: constant half-spread around the mid, no inventory or volatility input.

With ``half_spread`` equal to half a tick on a one-tick market this joins the
best bid and best ask, which is the most aggressive passive quote possible.
"""
from __future__ import annotations

from lobx.strategy.base import MarketState, QuotePair, Strategy, passive_pair


class FixedSpreadStrategy(Strategy):
    def __init__(self, half_spread: float = 0.05, qty: float = 0.001, label: str | None = None) -> None:
        if half_spread <= 0 or qty <= 0:
            raise ValueError("half_spread and qty must be positive")
        self.half_spread = half_spread
        self.qty = qty
        self.label = label

    def on_tick(self, state: MarketState, position: float) -> QuotePair:
        mid = state.mid
        return passive_pair(state, mid - self.half_spread, mid + self.half_spread, self.qty, self.qty)
