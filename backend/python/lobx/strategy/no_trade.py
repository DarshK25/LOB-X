"""A zero-risk benchmark for validating backtest accounting."""
from __future__ import annotations

from lobx.strategy.base import MarketState, QuotePair, Strategy


class NoTradeStrategy(Strategy):
    """Never quote; its P&L must remain exactly zero in every scenario."""

    def on_tick(self, state: MarketState) -> QuotePair:
        return QuotePair(bid=None, ask=None)
