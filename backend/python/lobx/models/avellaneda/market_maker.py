"""
Avellaneda-Stoikov Market Maker.

Puts together reservation_price + optimal_spread to produce bid/ask quotes
that are then submitted to the C++ order book each tick.
"""
from __future__ import annotations
import lobx_cpp
from lobx.simulation.event_loop import BaseTrader
from lobx.models.avellaneda.reservation_price import reservation_price
from lobx.models.avellaneda.optimal_spread import half_spread


class AvellanedaStoikovMM(BaseTrader):
    """
    AS optimal market maker.

    Parameters
    ----------
    book            : shared C++ OrderBook
    trader_id_start : first order ID
    gamma           : risk-aversion (higher → tighter quotes when inventory builds)
    sigma           : per-tick mid-price volatility estimate
    kappa           : order-flow intensity estimate
    session_ticks   : total session length in ticks
    qty             : size of each side quote
    """

    def __init__(
        self,
        book: lobx_cpp.OrderBook,
        trader_id_start: int,
        gamma: float = 0.1,
        sigma: float = 1.0,
        kappa: float = 1.5,
        session_ticks: int = 1000,
        qty: int = 5,
    ) -> None:
        super().__init__(book, trader_id_start)
        self._gamma   = gamma
        self._sigma   = sigma
        self._kappa   = kappa
        self._T       = session_ticks
        self._t       = 0
        self._qty     = qty
        self._inventory = 0.0
        self._bid_id: int | None = None
        self._ask_id: int | None = None

    def step(self) -> list[lobx_cpp.Trade]:
        self._t += 1
        time_remaining = max(0.0, (self._T - self._t) / self._T)

        mid = self.book.mid_price()
        if mid is None:
            return []

        r   = reservation_price(mid, self._inventory, self._gamma, self._sigma, time_remaining)
        hs  = half_spread(self._gamma, self._sigma, time_remaining, self._kappa)

        bid_px = round(r - hs)
        ask_px = round(r + hs)

        if bid_px >= ask_px:
            ask_px = bid_px + 1  # ensure valid spread

        # Cancel stale quotes.
        if self._bid_id is not None:
            self.book.cancel(self._bid_id)
        if self._ask_id is not None:
            self.book.cancel(self._ask_id)

        self._bid_id = self._new_id()
        self._ask_id = self._new_id()

        trades: list[lobx_cpp.Trade] = []
        trades += self.book.add_limit_order(lobx_cpp.Order(self._bid_id, bid_px, self._qty, True))
        trades += self.book.add_limit_order(lobx_cpp.Order(self._ask_id, ask_px, self._qty, False))

        # Update inventory from fills.
        for t in trades:
            if t.buy_id == self._bid_id:
                self._inventory += t.qty
            elif t.sell_id == self._ask_id:
                self._inventory -= t.qty

        return trades
