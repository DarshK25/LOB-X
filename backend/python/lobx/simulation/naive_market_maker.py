"""
NaiveMarketMaker — baseline market maker that quotes a fixed spread.

Posts one bid and one ask at (mid ± half_spread), refreshing every tick.
This is a *baseline*, not a real strategy — it has no inventory control.
See lobx.models.avellaneda for the AS optimal market making strategy.
"""
from __future__ import annotations
import lobx_cpp
from lobx.simulation.event_loop import BaseTrader


class NaiveMarketMaker(BaseTrader):
    def __init__(
        self,
        book: lobx_cpp.OrderBook,
        mid_price: int,
        trader_id_start: int,
        half_spread: int = 2,
        qty: int = 5,
    ) -> None:
        super().__init__(book, trader_id_start)
        self._mid        = mid_price
        self._half       = half_spread
        self._qty        = qty
        self._bid_id: int | None = None
        self._ask_id: int | None = None

    def step(self) -> list[lobx_cpp.Trade]:
        # Cancel previous quotes.
        if self._bid_id is not None:
            self.book.cancel(self._bid_id)
        if self._ask_id is not None:
            self.book.cancel(self._ask_id)

        mid = self.book.mid_price()
        ref = round(mid) if mid is not None else self._mid

        bid_px = ref - self._half
        ask_px = ref + self._half

        self._bid_id = self._new_id()
        self._ask_id = self._new_id()

        bid_order = lobx_cpp.Order(self._bid_id, bid_px, self._qty, True)
        ask_order = lobx_cpp.Order(self._ask_id, ask_px, self._qty, False)

        trades: list[lobx_cpp.Trade] = []
        trades += self.book.add_limit_order(bid_order)
        trades += self.book.add_limit_order(ask_order)
        return trades
