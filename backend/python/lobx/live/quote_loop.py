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
from lobx.analytics.pnl_waterfall import summarize_pnl_waterfall

if TYPE_CHECKING:
    from lobx.market_data.collector import Collector
    from lobx.market_data.trade_stream import TradeTick
    from lobx.terminal.telemetry import TelemetryCollector

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
        telemetry: "TelemetryCollector | None" = None,
    ) -> None:
        self.collector     = collector
        self.strategy      = strategy
        self.shadow_engine = ShadowQuoteEngine(assumptions=execution_assumptions or ExecutionAssumptions())
        self.risk_manager  = MarketMakingRiskManager(risk_config)
        self.vol_estimator = EWMAVolatilityEstimator(decay=0.98)
        self.telemetry     = telemetry

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
        self.shadow_engine.update_market_context(
            timestamp=int(event.get("E", 0)),
            mid=mid,
            best_bid=bid_p,
            best_ask=ask_p,
        )

        # Feed EWMA estimator.
        vol = self.vol_estimator.update(mid)
        self._last_mid = mid

        # Build frozen state snapshot.
        state = MarketState.from_book(
            timestamp=int(event.get("E", 0)),
            best_bid=bid_p,
            best_bid_qty=bid_q,
            best_ask=ask_p,
            best_ask_qty=ask_q,
            volatility=vol,
            inventory=self.shadow_engine.inventory.position,
            log_return=self.vol_estimator.last_log_return,
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
        risk_events_before = len(self.risk_manager.events)
        quotes = self.risk_manager.apply_quotes(
            quotes,
            self.shadow_engine.inventory.position,
            timestamp=state.timestamp,
            market_mid=mid,
            # update_quotes() cancel-replaces the current pair, so the
            # projected open-order count is the proposed pair, not old orders.
            open_orders=0,
        )
        for risk_event in self.risk_manager.events[risk_events_before:]:
            self.shadow_engine.emit_event(risk_event)
        self.shadow_engine.record_quote_decision(
            strategy=self.strategy.name(),
            state=state,
            quotes=quotes,
            details=getattr(self.strategy, "last_quote_audit", {}),
        )
        book = self.collector.depth_mgr.book
        bid_ahead = book.bids.get(quotes.bid.price, 0.0) if quotes.bid else 0.0
        ask_ahead = book.asks.get(quotes.ask.price, 0.0) if quotes.ask else 0.0
        self.shadow_engine.update_quotes(quotes, state.timestamp, bid_ahead, ask_ahead)

        if self.telemetry is not None:
            from lobx.terminal.bridge import publish_tick
            publish_tick(
                self.telemetry,
                state=state,
                strategy_name=self.strategy.name(),
                quotes=quotes,
                audit=getattr(self.strategy, "last_quote_audit", {}),
                engine=self.shadow_engine,
                mark_price=mid,
                bids=self._top_depth(book.bids, reverse=True),
                asks=self._top_depth(book.asks, reverse=False),
            )

        # Periodic status log.
        self._tick_count += 1
        if self._tick_count % LOG_EVERY_N == 0:
            fills = self.shadow_engine.fill_count
            pos   = self.shadow_engine.inventory.position
            rpnl  = self.shadow_engine.inventory.realized_pnl
            upnl  = self.shadow_engine.inventory.unrealized_pnl(mid)
            latest_blocks = [
                event for event in self.shadow_engine.events
                if event.get("event_type") == "RISK_BLOCK"
            ]
            block_reason = latest_blocks[-1]["reason"] if latest_blocks else None
            bid_s = f"{quotes.bid.price:.2f}" if quotes.bid else f"BLOCKED[{block_reason}]"
            ask_s = f"{quotes.ask.price:.2f}" if quotes.ask else f"BLOCKED[{block_reason}]"
            logger.info(
                "[%s tick=%d]  mid=%.2f  vol=%.6f  "
                "quote: BID=%s  ASK=%s  |  "
                "pos=%.6f  fills=%d  rPnL=%.4f  uPnL=%.4f",
                self.strategy.name(), self._tick_count, mid, vol,
                bid_s, ask_s, pos, fills, rpnl, upnl,
            )

    @staticmethod
    def _top_depth(levels, *, reverse: bool, n: int = 10) -> list[tuple[float, float]]:
        """Top-N (price, qty) levels, best price first.

        ``levels`` is a price->qty mapping sorted ascending by price (SortedDict).
        Bids want the highest prices first (reverse=True); asks the lowest.
        """
        try:
            items = list(levels.items())
        except AttributeError:
            return []
        ordered = list(reversed(items)) if reverse else items
        return [(float(p), float(q)) for p, q in ordered[:n]]

    def _on_trade(self, trade: "TradeTick") -> None:
        """Called by Collector after every parsed trade tick."""
        self.shadow_engine.on_trade(trade)
        if self.telemetry is not None and self._last_mid is not None:
            from lobx.terminal.bridge import publish_execution
            publish_execution(
                self.telemetry,
                engine=self.shadow_engine,
                mark_price=self._last_mid,
                timestamp=trade.event_time,
            )

    # ── Session summary ───────────────────────────────────────────────────────

    def summary(self) -> dict:
        """Return a dict summary of the session for printing at shutdown."""
        inv  = self.shadow_engine.inventory
        mid  = self._last_mid or 0.0
        summary = {
            "strategy":      self.strategy.name(),
            "ticks":         self._tick_count,
            "fills":         self.shadow_engine.fill_count,
            "position":      inv.position,
            "avg_entry":     inv.avg_entry_price,
            "realized_pnl":  inv.realized_pnl,
            "unrealized_pnl":inv.unrealized_pnl(mid),
            "fees_paid":     self.shadow_engine.fees_paid,
            "total_pnl":     self.shadow_engine.net_pnl(mid),
            "max_inventory": inv.max_inventory,
            "average_inventory": inv.average_inventory,
            "inventory_variance": inv.inventory_variance,
            "inventory_half_life_ms": inv.inventory_half_life_ms,
            "time_spent_long_ms": inv.time_spent_long_ms,
            "time_spent_short_ms": inv.time_spent_short_ms,
            "inventory_distribution": inv.inventory_distribution,
            "risk_halted":   self.risk_manager.halted,
            "risk_halt_reason": self.risk_manager.halt_reason,
        }
        summary.update(summarize_pnl_waterfall(
            self.shadow_engine.fills,
            [],
            realized_pnl=inv.realized_pnl,
            unrealized_pnl=inv.unrealized_pnl(mid),
            fees=self.shadow_engine.fees_paid,
        ))
        return summary
