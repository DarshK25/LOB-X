"""
InstitutionalTrader — executes a large order using TWAP/VWAP slicing.

Splits a large parent order into child orders submitted over `horizon` ticks,
mimicking an institutional execution algorithm (simplified Almgren-Chriss).
"""
from __future__ import annotations
import math
import lobx_cpp
from lobx.simulation.event_loop import BaseTrader


class InstitutionalTrader(BaseTrader):
    def __init__(
        self,
        book: lobx_cpp.OrderBook,
        trader_id_start: int,
        total_qty: int,
        is_buy: bool,
        horizon_ticks: int = 100,
        aggression: float = 0.0,  # 0 = passive TWAP, 1 = aggressive
    ) -> None:
        super().__init__(book, trader_id_start)
        self._remaining  = total_qty
        self._is_buy     = is_buy
        self._horizon    = horizon_ticks
        self._ticks_left = horizon_ticks
        self._aggression = aggression

    @property
    def done(self) -> bool:
        return self._remaining <= 0 or self._ticks_left <= 0

    def step(self) -> list[lobx_cpp.Trade]:
        if self.done:
            return []

        # Child qty: floor of remaining / ticks left (TWAP schedule)
        child_qty = max(1, math.ceil(self._remaining / max(1, self._ticks_left)))
        child_qty = min(child_qty, self._remaining)

        mid = self.book.mid_price()
        if mid is None:
            self._ticks_left -= 1
            return []

        # Passive: post at best; aggressive: cross spread slightly.
        if self._is_buy:
            best = self.book.best_ask()
            price = int(best or mid) + round(self._aggression)
        else:
            best = self.book.best_bid()
            price = int(best or mid) - round(self._aggression)

        order = lobx_cpp.Order(self._new_id(), price, child_qty, self._is_buy)
        trades = self.book.add_limit_order(order)
        filled = sum(t.qty for t in trades)
        self._remaining  -= filled
        self._ticks_left -= 1
        return trades
