"""Zero-activity benchmark: never quotes, so PnL is exactly zero."""
from __future__ import annotations

from lobx.strategy.base import NO_QUOTES, MarketState, QuotePair, Strategy


class NoTradeStrategy(Strategy):
    def __init__(self, qty: float = 0.001, label: str | None = None) -> None:
        self.qty = qty
        self.label = label

    def on_tick(self, state: MarketState, position: float) -> QuotePair:
        return NO_QUOTES
