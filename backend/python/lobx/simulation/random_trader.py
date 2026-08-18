"""
RandomTrader — submits random limit orders each tick.

This is the simplest archetype: random side, random price ±5 ticks from mid,
random qty 1–20. Serves as the baseline noise process.
"""
from __future__ import annotations
import random
import lobx_cpp
from lobx.simulation.event_loop import BaseTrader


class RandomTrader(BaseTrader):
    """Uniformly random noise trader."""

    def __init__(
        self,
        book: lobx_cpp.OrderBook,
        mid_price: int,
        trader_id_start: int,
        price_offset: int = 5,
        max_qty: int = 20,
        seed: int | None = None,
    ) -> None:
        super().__init__(book, trader_id_start)
        self.mid           = mid_price
        self.price_offset  = price_offset
        self.max_qty       = max_qty
        self._rng          = random.Random(seed)

    def step(self) -> list[lobx_cpp.Trade]:
        price  = self.mid + self._rng.randint(-self.price_offset, self.price_offset)
        qty    = self._rng.randint(1, self.max_qty)
        is_buy = self._rng.random() < 0.5
        order  = lobx_cpp.Order(self._new_id(), price, qty, is_buy)
        return self.book.add_limit_order(order)
