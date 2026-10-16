"""Terminal telemetry bridge: pipeline state must reach the terminal unchanged.

These tests exercise the canonical path
    MarketState + ShadowQuoteEngine + PortfolioEngine -> TelemetryCollector
and assert the terminal snapshots equal the accounting authority (the
PortfolioEngine), that fills are counted by real aggressor side, and that the
ShadowQuoteEngine ledger is the single event source (no duplication on re-sync).
"""
from __future__ import annotations

import pytest

from lobx.execution.shadow_engine import ExecutionAssumptions, ShadowQuoteEngine
from lobx.market_data.trade_stream import TradeTick
from lobx.strategy.base import MarketState, Quote, QuotePair
from lobx.terminal import bridge
from lobx.terminal.telemetry import TelemetryCollector

MAKER_FEE = 0.0002


def _state(inventory: float = 0.0) -> MarketState:
    return MarketState.from_book(
        timestamp=1000,
        best_bid=99.99,
        best_bid_qty=2.0,
        best_ask=100.01,
        best_ask_qty=1.0,
        volatility=0.5,
        inventory=inventory,
    )


def _engine() -> ShadowQuoteEngine:
    engine = ShadowQuoteEngine(
        assumptions=ExecutionAssumptions(maker_fee_rate=MAKER_FEE)
    )
    engine.update_market_context(timestamp=1000, mid=100.0, best_bid=99.99, best_ask=100.01)
    quotes = QuotePair(bid=Quote(99.99, 0.001), ask=Quote(100.01, 0.001))
    engine.update_quotes(quotes, timestamp=1000, bid_queue_ahead=0.0, ask_queue_ahead=0.0)
    return engine


def test_publish_tick_reflects_market_and_strategy():
    engine = _engine()
    collector = TelemetryCollector()
    audit = {"reservation_price": 99.997, "model_full_spread": 0.02, "gamma": 0.1,
             "effective_kappa": 1.5, "sigma": 0.5, "time_remaining_seconds": 300.0}
    quotes = QuotePair(bid=Quote(99.99, 0.001), ask=Quote(100.01, 0.001))

    bridge.publish_tick(
        collector, state=_state(), strategy_name="AS", quotes=quotes,
        audit=audit, engine=engine, mark_price=100.0,
        bids=[(99.99, 2.0)], asks=[(100.01, 1.0)],
    )

    assert collector.market.mid == 100.0
    assert collector.market.spread == pytest.approx(0.02)
    assert collector.market.bids == [(99.99, 2.0)]
    assert collector.strategy.reservation_price == 99.997
    assert collector.strategy.kappa == 1.5  # effective_kappa preferred
    assert collector.execution.total_fills == 0
    # No trade yet: portfolio flat, PnL zero, terminal agrees with the engine.
    assert collector.portfolio.net_pnl == pytest.approx(engine.portfolio.net_pnl(100.0))
    assert collector.portfolio.net_pnl == pytest.approx(0.0)


def test_fill_flows_to_terminal_with_engine_accounting_authority():
    engine = _engine()
    collector = TelemetryCollector()
    audit = {"reservation_price": 99.997}
    quotes = QuotePair(bid=Quote(99.99, 0.001), ask=Quote(100.01, 0.001))
    bridge.publish_tick(collector, state=_state(), strategy_name="AS", quotes=quotes,
                        audit=audit, engine=engine, mark_price=100.0)

    # Sell aggressor at 99.99 hits our resting bid -> we BUY 0.001.
    engine.on_trade(TradeTick(1001, 1, 99.99, 0.001, is_buyer_maker=True, symbol="BTCUSDT"))
    bridge.publish_execution(collector, engine=engine, mark_price=100.0, timestamp=1001)

    assert engine.fill_count == 1
    assert collector.execution.total_fills == 1
    assert collector.execution.bid_fills == 1
    assert collector.execution.ask_fills == 0

    # The terminal's PnL must equal the PortfolioEngine, not a recomputation.
    expected = engine.portfolio.snapshot(100.0)
    assert collector.portfolio.net_pnl == pytest.approx(expected["net_pnl"])
    assert collector.portfolio.realized_pnl == pytest.approx(expected["realized_pnl"])
    assert collector.portfolio.fees == pytest.approx(99.99 * 0.001 * MAKER_FEE)
    assert collector.portfolio.inventory == pytest.approx(0.001)


def test_event_ledger_is_single_source_no_duplicate_on_resync():
    engine = _engine()
    collector = TelemetryCollector()
    engine.on_trade(TradeTick(1001, 1, 99.99, 0.001, is_buyer_maker=True, symbol="BTCUSDT"))

    collector.sync_events(engine.events)
    first = len(collector.events)
    fills = [e for e in collector.events if e.event_type == "FILL"]
    assert len(fills) == 1
    assert fills[0].data["side"] == "buy"
    assert fills[0].data["price"] == 99.99
    assert fills[0].data["qty"] == pytest.approx(0.001)

    # Re-syncing the same ledger must not re-append already-seen events.
    collector.sync_events(engine.events)
    assert len(collector.events) == first
