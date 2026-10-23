"""Integration regressions for depth notifications in live queue execution."""
from __future__ import annotations

import pytest

from lobx.execution.shadow_engine import ExecutionAssumptions, ShadowQuoteEngine
from lobx.live.quote_loop import StrategyRunner
from lobx.market_data.trade_stream import TradeTick
from lobx.strategy.base import Quote, QuotePair, Strategy


class _Book:
    def __init__(self) -> None:
        self.bids = {100.0: 1.0}
        self.asks = {101.0: 1.0}

    def best_bid(self) -> tuple[float, float]:
        price = max(self.bids)
        return price, self.bids[price]

    def best_ask(self) -> tuple[float, float]:
        price = min(self.asks)
        return price, self.asks[price]


class _Collector:
    engine_mirror = None

    def __init__(self) -> None:
        self.depth_mgr = type("DepthManager", (), {"book": _Book()})()
        self.book_callback = None
        self.trade_callback = None

    def on_book_update(self, callback) -> None:
        self.book_callback = callback

    def on_trade(self, callback) -> None:
        self.trade_callback = callback


class _Strategy(Strategy):
    def on_tick(self, state):
        return QuotePair(
            bid=Quote(99.0, 0.001),
            ask=Quote(102.0, 0.001),
        )


def test_strategy_runner_forwards_absolute_quote_level_depth_updates():
    collector = _Collector()
    runner = StrategyRunner(
        collector,
        _Strategy(),
        execution_assumptions=ExecutionAssumptions(queue_model="conservative"),
    )

    # Collector callbacks receive events after the local book has applied the
    # absolute update. The fake book is intentionally unchanged because the
    # queue notification itself is the behavior under test.
    collector.book_callback({"E": 1_000, "b": [["99.0", "2.0"]]})
    assert runner.shadow_engine.resting_bid.queue_ahead_qty == pytest.approx(2.0)

    collector.book_callback({"E": 2_000, "b": [["99.0", "1.0"]]})
    assert runner.shadow_engine.resting_bid.queue_ahead_qty < 2.0


def test_scripted_depth_shadow_fills_before_conservative_then_depth_advances_queue():
    """A fixed script separates trade crossing from L2 queue advancement."""
    quotes = QuotePair(bid=Quote(99.0, 0.1), ask=Quote(101.0, 0.1))
    trade = TradeTick(
        event_time=2,
        trade_id=1,
        price=99.0,
        qty=0.1,
        is_buyer_maker=True,
        symbol="BTCUSDT",
    )

    shadow = ShadowQuoteEngine(assumptions=ExecutionAssumptions(queue_model="shadow"))
    conservative = ShadowQuoteEngine(
        assumptions=ExecutionAssumptions(queue_model="conservative")
    )
    for engine in (shadow, conservative):
        engine.on_depth_update("bid", 99.0, 2.0, timestamp=0)
        engine.update_quotes(quotes, timestamp=1)
        engine.on_trade(trade)

    assert shadow.fill_count == 1
    assert conservative.fill_count == 0
    queue_before_depth = conservative.resting_bid.queue_ahead_qty

    conservative.on_depth_update("bid", 99.0, 1.5, timestamp=3)

    assert conservative.resting_bid.queue_ahead_qty < queue_before_depth
    assert conservative.depth_advance_events == 1
    assert shadow.depth_advance_events == 0
