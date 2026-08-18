"""
Simulator — orchestrates the event loop over all registered traders.

Usage::

    from lobx.simulation import Simulator
    from lobx.simulation.random_trader import RandomTrader

    sim = Simulator(mid_price=10_000)
    sim.register(RandomTrader(sim.book, 10_000, trader_id_start=1))
    results = sim.run(ticks=1000)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import lobx_cpp

from lobx.simulation.market_clock import MarketClock

if TYPE_CHECKING:
    from lobx.simulation.event_loop import BaseTrader

log = logging.getLogger(__name__)


@dataclass
class SimulationResult:
    ticks: int
    total_trades: int
    total_volume: int
    trade_log: list[lobx_cpp.Trade] = field(default_factory=list)


class Simulator:
    """Main simulation orchestrator."""

    def __init__(self, mid_price: int, tick_ms: int = 100) -> None:
        self.book    = lobx_cpp.OrderBook()
        self.clock   = MarketClock(tick_ms=tick_ms)
        self.traders: list[BaseTrader] = []
        self._all_trades: list[lobx_cpp.Trade] = []
        self._mid    = mid_price

    def register(self, trader: BaseTrader) -> None:
        self.traders.append(trader)
        log.debug("Registered trader %s", type(trader).__name__)

    def run(self, ticks: int) -> SimulationResult:
        """Run the simulation for `ticks` discrete time steps."""
        self._all_trades.clear()
        self.clock.reset()

        for _ in range(ticks):
            self.clock.advance()
            for trader in self.traders:
                trades = trader.step()
                self._all_trades.extend(trades)

        total_vol = sum(t.qty for t in self._all_trades)
        log.info(
            "Simulation complete: %d ticks, %d trades, volume=%d",
            ticks, len(self._all_trades), total_vol,
        )
        return SimulationResult(
            ticks=ticks,
            total_trades=len(self._all_trades),
            total_volume=total_vol,
            trade_log=list(self._all_trades),
        )
