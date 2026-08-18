"""
Almgren-Chriss Executor.

Submits child orders to the C++ book following the AC optimal trajectory.
"""
from __future__ import annotations
import lobx_cpp
from lobx.simulation.event_loop import BaseTrader
from lobx.models.almgren.trajectory import optimal_trajectory, trade_list


class AlmgrenChrissExecutor(BaseTrader):
    """Executes a liquidation using the Almgren-Chriss optimal schedule."""

    def __init__(
        self,
        book: lobx_cpp.OrderBook,
        trader_id_start: int,
        total_shares: int,
        is_sell: bool,
        T: int,
        sigma: float,
        eta: float,
        gamma: float,
        lambda_: float,
    ) -> None:
        super().__init__(book, trader_id_start)
        self._is_sell = is_sell
        traj    = optimal_trajectory(float(total_shares), T, sigma, eta, gamma, lambda_)
        self._schedule = [max(0, round(q)) for q in trade_list(traj)]
        self._tick = 0

    @property
    def done(self) -> bool:
        return self._tick >= len(self._schedule)

    def step(self) -> list[lobx_cpp.Trade]:
        if self.done:
            return []

        child_qty = self._schedule[self._tick]
        self._tick += 1

        if child_qty == 0:
            return []

        mid = self.book.mid_price()
        if mid is None:
            return []

        if self._is_sell:
            best = self.book.best_bid()
            price = int(best or mid)
            is_buy = False
        else:
            best = self.book.best_ask()
            price = int(best or mid)
            is_buy = True

        order = lobx_cpp.Order(self._new_id(), price, child_qty, is_buy)
        return self.book.add_limit_order(order)
