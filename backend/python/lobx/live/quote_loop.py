"""
live/quote_loop.py — StrategyRunner: wires Collector → Strategy → ShadowQuoteEngine.

Responsibilities
────────────────
1. Register callbacks on the Collector (on_book_update, on_trade).
2. On each book update:
     a. Read best bid/ask from the C++ engine (or Python reference book as
        fallback if the C++ engine isn't compiled).
     b. Feed the mid-price into the EWMA volatility estimator.
     c. Build a frozen MarketState.
     d. Call strategy.on_tick(state) → QuotePair.
     e. Pass quotes to ShadowQuoteEngine.update_quotes().
3. On each trade tick → ShadowQuoteEngine.on_trade() for fill checks.
4. Periodic logging every LOG_EVERY_N ticks showing live strategy state.

Book priority (C++ engine first):
  The C++ engine book is what strategies were built to quote against.
  If it's not available (machine without C++ toolchain / pure data-collection
  mode), the Python reference book is used automatically — same state, just
  slower.  The strategy code doesn't know or care which book is providing
  the bid/ask.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from lobx.strategy.base import MarketState, Strategy
from lobx.execution.shadow_engine import ExecutionAssumptions, ShadowQuoteEngine
from lobx.analytics.volatility import EWMAVolatilityEstimator
from lobx.risk import MarketMakingRiskConfig, MarketMakingRiskManager

if TYPE_CHECKING:
    from lobx.market_data.collector import Collector
    from lobx.market_data.trade_stream import TradeTick

logger = logging.getLogger(__name__)

# Log a status line every this many synced depth events (~10s at BTC rates).
LOG_EVERY_N = 100


class StrategyRunner:
    """Connects a live Collector to one Strategy + one ShadowQuoteEngine.

    Parameters
    ----------
    collector : running Collector instance (call BEFORE collector.run())
    strategy  : any Strategy implementation (FixedSpreadStrategy, AS, ...)
    """

    def __init__(
        self,
        collector: "Collector",
        strategy: Strategy,
        execution_assumptions: ExecutionAssumptions | None = None,
        risk_config: MarketMakingRiskConfig | None = None,
    ) -> None:
        self.collector     = collector
        self.strategy      = strategy
        self.shadow_engine = ShadowQuoteEngine(assumptions=execution_assumptions or ExecutionAssumptions())
        self.risk_manager  = MarketMakingRiskManager(risk_config)
        self.vol_estimator = EWMAVolatilityEstimator(decay=0.98)

        self._tick_count  = 0
        self._last_mid: float | None = None

        # Wire callbacks — must happen BEFORE collector.run() is awaited.
        collector.on_book_update(self._on_book_update)
        collector.on_trade(self._on_trade)

        logger.info(
            "StrategyRunner initialised: strategy=%s  engine_mirror=%s",
            strategy.name(),
            "C++" if collector.engine_mirror is not None else "Python reference book",
        )

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _get_best_bid_ask(self) -> tuple[tuple[float, float] | None, tuple[float, float] | None]:
        """Read best bid/ask from C++ engine (preferred) or Python book.

        Returns (bid, ask) each as (price, qty) tuples, or None if not ready.
        """
        # C++ engine first — this is the book strategies are meant to use.
        if self.collector.engine_mirror is not None:
            engine_bid = self.collector.engine_mirror.book.best_bid()
            engine_ask = self.collector.engine_mirror.book.best_ask()
            if engine_bid is not None and engine_ask is not None:
                # C++ engine returns integer ticks — convert back to USD.
                from lobx.market_data.engine_mirror import TICKS_PER_UNIT
                bid = (engine_bid / TICKS_PER_UNIT, 0.0)   # qty not tracked in engine mirror
                ask = (engine_ask / TICKS_PER_UNIT, 0.0)
                return bid, ask

        # Fallback: Python reference book.
        book = self.collector.depth_mgr.book
        return book.best_bid(), book.best_ask()

    # ── Callbacks ─────────────────────────────────────────────────────────────

    def _on_book_update(self, event: dict) -> None:
        """Called by Collector after every synced depth event."""
        bid_info, ask_info = self._get_best_bid_ask()
        if bid_info is None or ask_info is None:
            return   # book not ready yet

        bid_p, bid_q = bid_info
        ask_p, ask_q = ask_info
        mid = (bid_p + ask_p) / 2.0

        # Feed EWMA estimator.
        vol = self.vol_estimator.update(mid)
        self._last_mid = mid

        # Build frozen state snapshot.
        state = MarketState(
            timestamp=int(event.get("E", 0)),
            mid_price=mid,
            best_bid=bid_p,
            best_bid_qty=bid_q,
            best_ask=ask_p,
            best_ask_qty=ask_q,
            spread=round(ask_p - bid_p, 8),
            volatility=vol,
            inventory=self.shadow_engine.inventory.position,
        )

        # Risk is updated before submitting a new quote. It is deliberately
        # outside the strategy so no strategy implementation can bypass it.
        self.risk_manager.update_equity(self.shadow_engine.net_pnl(mid))

        # Run strategy.
        try:
            quotes = self.strategy.on_tick(state)
        except Exception:
            logger.exception("Strategy.on_tick raised — skipping this tick")
            return

        # Apply hard limits, then pass displayed L2 quantity to the optional
        # queue-ahead approximation. The public feed is L2, so this is a
        # conservative scenario tool rather than true queue reconstruction.
        quotes = self.risk_manager.apply_quotes(quotes, self.shadow_engine.inventory.position)
        book = self.collector.depth_mgr.book
        bid_ahead = book.bids.get(quotes.bid.price, 0.0) if quotes.bid else 0.0
        ask_ahead = book.asks.get(quotes.ask.price, 0.0) if quotes.ask else 0.0
        self.shadow_engine.update_quotes(quotes, state.timestamp, bid_ahead, ask_ahead)

        # Periodic status log.
        self._tick_count += 1
        if self._tick_count % LOG_EVERY_N == 0:
            fills = self.shadow_engine.fill_count
            pos   = self.shadow_engine.inventory.position
            rpnl  = self.shadow_engine.inventory.realized_pnl
            upnl  = self.shadow_engine.inventory.unrealized_pnl(mid)
            bid_s = f"{quotes.bid.price:.2f}" if quotes.bid else "—"
            ask_s = f"{quotes.ask.price:.2f}" if quotes.ask else "—"
            logger.info(
                "[%s tick=%d]  mid=%.2f  vol=%.6f  "
                "quote: bid=%s ask=%s  |  "
                "pos=%.6f  fills=%d  rPnL=%.4f  uPnL=%.4f",
                self.strategy.name(), self._tick_count, mid, vol,
                bid_s, ask_s, pos, fills, rpnl, upnl,
            )

    def _on_trade(self, trade: "TradeTick") -> None:
        """Called by Collector after every parsed trade tick."""
        self.shadow_engine.on_trade(trade)

    # ── Session summary ───────────────────────────────────────────────────────

    def summary(self) -> dict:
        """Return a dict summary of the session for printing at shutdown."""
        inv  = self.shadow_engine.inventory
        mid  = self._last_mid or 0.0
        return {
            "strategy":      self.strategy.name(),
            "ticks":         self._tick_count,
            "fills":         self.shadow_engine.fill_count,
            "position":      inv.position,
            "avg_entry":     inv.avg_entry_price,
            "realized_pnl":  inv.realized_pnl,
            "unrealized_pnl":inv.unrealized_pnl(mid),
            "fees_paid":     self.shadow_engine.fees_paid,
            "total_pnl":     self.shadow_engine.net_pnl(mid),
            "risk_halted":   self.risk_manager.halted,
            "risk_halt_reason": self.risk_manager.halt_reason,
        }
