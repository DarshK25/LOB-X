"""
MeanReversionTrader — fades large price moves back toward a reference price.

Submits a contra-directional order when mid deviates more than `threshold`
ticks from the long-run mean.
"""
from __future__ import annotations
from collections import deque
import lobx_cpp
from lobx.simulation.event_loop import BaseTrader


class MeanReversionTrader(BaseTrader):
    def __init__(
        self,
        book: lobx_cpp.OrderBook,
        mid_price: int,
        trader_id_start: int,
        window: int = 50,
        threshold: float = 3.0,  # ticks
        qty: int = 8,
    ) -> None:
        super().__init__(book, trader_id_start)
        self._ref    = float(mid_price)
        self._history: deque[float] = deque(maxlen=window)
        self._history.append(float(mid_price))
        self._thresh = threshold
        self._qty    = qty

    def step(self) -> list[lobx_cpp.Trade]:
        mid = self.book.mid_price()
        if mid is None:
            return []

        self._history.append(mid)
        mean   = sum(self._history) / len(self._history)
        delta  = mid - mean

        if abs(delta) < self._thresh:
            return []  # No trade if deviation is small.

        is_buy = delta < 0  # Mid is below mean → expect reversion up → buy
        price  = round(mid)
        order  = lobx_cpp.Order(self._new_id(), price, self._qty, is_buy)
        return self.book.add_limit_order(order)
