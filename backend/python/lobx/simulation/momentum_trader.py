"""
MomentumTrader — buys when price trends up, sells when it trends down.

Tracks a simple moving average of recent mid-prices.
When current mid > SMA: submit a buy.
When current mid < SMA: submit a sell.
"""
from __future__ import annotations
import random
from collections import deque
import lobx_cpp
from lobx.simulation.event_loop import BaseTrader


class MomentumTrader(BaseTrader):
    def __init__(
        self,
        book: lobx_cpp.OrderBook,
        mid_price: int,
        trader_id_start: int,
        window: int = 20,
        aggressiveness: int = 2,  # ticks beyond best to hit
        qty: int = 10,
        seed: int | None = None,
    ) -> None:
        super().__init__(book, trader_id_start)
        self._history: deque[float] = deque(maxlen=window)
        self._history.append(float(mid_price))
        self._aggressive = aggressiveness
        self._qty        = qty
        self._rng        = random.Random(seed)

    def step(self) -> list[lobx_cpp.Trade]:
        mid = self.book.mid_price()
        if mid is None:
            return []

        self._history.append(mid)
        if len(self._history) < self._history.maxlen:
            return []

        sma    = sum(self._history) / len(self._history)
        is_buy = mid > sma
        # Aggressive: cross the spread by `aggressiveness` ticks.
        if is_buy:
            best = self.book.best_ask()
            if best is None:
                return []
            price = int(best) + self._aggressive
        else:
            best = self.book.best_bid()
            if best is None:
                return []
            price = int(best) - self._aggressive

        order = lobx_cpp.Order(self._new_id(), price, self._qty, is_buy)
        return self.book.add_limit_order(order)
