"""A transparent inventory-aware baseline for research.

Unlike Avellaneda--Stoikov, this strategy does not claim that its skew comes
from a calibrated arrival-intensity model.  It exposes the inventory response
directly in USD per BTC so it can be tested and rejected honestly.
"""
from __future__ import annotations

from lobx.strategy.base import MarketState, Quote, QuotePair, Strategy


class InventorySkewStrategy(Strategy):
    """Quote a fixed spread around a mid shifted away from inventory.

    A long position lowers both quotes, making the ask more attractive and the
    bid less attractive. A short position does the reverse. Hard inventory
    limits remain the risk manager's responsibility.
    """

    def __init__(
        self,
        half_spread: float = 0.5,
        qty: float = 0.001,
        skew_usd_per_btc: float = 50.0,
    ) -> None:
        if half_spread <= 0 or qty <= 0 or skew_usd_per_btc < 0:
            raise ValueError("half_spread and qty must be positive; skew cannot be negative")
        self.half_spread = half_spread
        self.qty = qty
        self.skew_usd_per_btc = skew_usd_per_btc

    def on_tick(self, state: MarketState) -> QuotePair:
        reservation = state.mid_price - state.inventory * self.skew_usd_per_btc
        bid = round(reservation - self.half_spread, 2)
        ask = round(reservation + self.half_spread, 2)
        if bid >= ask:
            ask = round(bid + 0.01, 2)
        return QuotePair(bid=Quote(bid, self.qty), ask=Quote(ask, self.qty))
