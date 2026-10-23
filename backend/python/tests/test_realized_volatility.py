"""Focused tests for elapsed-time rolling realized volatility."""
from __future__ import annotations

import math

import pytest

from lobx.analytics.realized_volatility import TimestampAwareRollingRealizedVolatility
from lobx.live.quote_loop import StrategyRunner
from lobx.strategy.base import Quote, QuotePair, Strategy


class _Book:
    def __init__(self, bid: float, ask: float) -> None:
        self.bids = {bid: 1.0}
        self.asks = {ask: 1.0}

    def best_bid(self) -> tuple[float, float]:
        price = max(self.bids)
        return price, self.bids[price]

    def best_ask(self) -> tuple[float, float]:
        price = min(self.asks)
        return price, self.asks[price]


class _Collector:
    engine_mirror = None

    def __init__(self) -> None:
        self.depth_mgr = type("DepthManager", (), {"book": _Book(100.0, 101.0)})()
        self.book_callback = None
        self.trade_callback = None

    def on_book_update(self, callback) -> None:
        self.book_callback = callback

    def on_trade(self, callback) -> None:
        self.trade_callback = callback


class _StateCaptureStrategy(Strategy):
    def __init__(self) -> None:
        self.states = []

    def on_tick(self, state):
        self.states.append(state)
        return QuotePair(
            bid=Quote(state.best_bid - 1.0, 0.001),
            ask=Quote(state.best_ask + 1.0, 0.001),
        )


def test_timestamp_window_evicts_by_milliseconds_not_observation_count():
    estimator = TimestampAwareRollingRealizedVolatility(window_ms=60_000)
    first = estimator.update(0, 100.0)
    second = estimator.update(10_000, 101.0)
    third = estimator.update(20_000, 102.0)

    assert first == 0.0
    first_return = math.log(101.0 / 100.0)
    second_return = math.log(102.0 / 101.0)
    assert second == pytest.approx(abs(first_return) / math.sqrt(10.0))
    assert third == pytest.approx(
        math.sqrt((first_return ** 2 + second_return ** 2) / 20.0)
    )
    assert estimator.observation_count == 2

    # Both earlier returns are outside the one-minute elapsed-time window.
    current = estimator.update(80_001, 103.0)
    assert current == pytest.approx(abs(math.log(103.0 / 102.0)) / math.sqrt(60.001))
    assert estimator.observation_count == 1


def test_timestamp_estimator_ignores_duplicate_and_late_observations():
    estimator = TimestampAwareRollingRealizedVolatility(window_ms=60_000)
    estimator.update(1_000, 100.0)
    estimator.update(2_000, 101.0)
    before = estimator.current()

    assert estimator.update(2_000, 120.0) == pytest.approx(before)
    assert estimator.update(1_500, 90.0) == pytest.approx(before)
    assert estimator.last_timestamp == 2_000
    assert estimator.last_log_return == pytest.approx(math.log(101.0 / 100.0))


def test_live_runner_places_timestamp_aware_volatility_in_market_state():
    collector = _Collector()
    strategy = _StateCaptureStrategy()
    runner = StrategyRunner(collector, strategy)

    collector.book_callback({"E": 1_000})
    collector.depth_mgr.book = _Book(101.0, 102.0)
    collector.book_callback({"E": 2_000})

    assert len(strategy.states) == 2
    assert strategy.states[0].volatility == 0.0
    expected_return = math.log(101.5 / 100.5)
    assert strategy.states[1].volatility == pytest.approx(abs(expected_return))
    assert strategy.states[1].log_return == pytest.approx(expected_return)
    assert runner.realized_vol_estimator.last_timestamp == 2_000
