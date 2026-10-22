"""Transparent inventory-aware baseline.

The reservation price moves ``skew_per_lot`` USD away from inventory for each
lot held (lot = order size). Long inventory lowers both quotes, so the ask gets
more aggressive and the bid less aggressive.
"""
from __future__ import annotations

from lobx.strategy.base import MarketState, QuotePair, Strategy, passive_pair


class InventorySkewStrategy(Strategy):
    def __init__(self, half_spread: float = 0.05, qty: float = 0.001,
                 skew_per_lot: float = 0.1, label: str | None = None) -> None:
        if half_spread <= 0 or qty <= 0 or skew_per_lot < 0:
            raise ValueError("half_spread and qty must be positive; skew cannot be negative")
        self.half_spread = half_spread
        self.qty = qty
        self.skew_per_lot = skew_per_lot
        self.label = label

    def on_tick(self, state: MarketState, position: float) -> QuotePair:
        r = state.mid - self.lots(position) * self.skew_per_lot
        return passive_pair(state, r - self.half_spread, r + self.half_spread, self.qty, self.qty)
