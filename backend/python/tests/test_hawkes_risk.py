"""Tests for opt-in Hawkes quote gating and cooldown behavior."""
from __future__ import annotations

import pytest

from lobx.execution.unwind import UnwindConfig
from lobx.risk import (
    HawkesParameters,
    HawkesRegime,
    HawkesRiskConfig,
    HawkesRiskController,
    MarketMakingRiskConfig,
    MarketMakingRiskManager,
)
from lobx.strategy.base import Quote, QuotePair
from lobx.market_data.trade_stream import TradeTick


def _quotes() -> QuotePair:
    return QuotePair(Quote(99.0, 0.001), Quote(101.0, 0.001))


def _controller() -> HawkesRiskController:
    return HawkesRiskController(
        config=HawkesRiskConfig(
            parameters=HawkesParameters(mu=1.0, alpha=1.5, beta=2.0),
            elevated_ratio=2.0,
            cascade_ratio=5.0,
            elevated_spread_multiplier=1.5,
            cooldown_ms=1_000,
        )
    )


def test_elevated_widens_quotes_and_duplicate_does_not_reexcite():
    controller = _controller()
    elevated = controller.on_trade("one", 1_000)
    assert elevated.regime is HawkesRegime.ELEVATED

    widened = controller.adjust_quotes(_quotes(), market_mid=100.0, decision=elevated)
    assert widened.bid.price == pytest.approx(98.5)
    assert widened.ask.price == pytest.approx(101.5)

    before = controller.intensity
    duplicate = controller.on_trade("one", 1_001)
    assert duplicate.intensity < before
    assert controller.duplicate_trade_count == 1


def test_cascade_pulls_quotes_then_cooldown_recovers():
    controller = _controller()
    controller.on_trade("one", 1_000)
    controller.on_trade("two", 1_010)
    cascade = controller.on_trade("three", 1_020)
    assert cascade.regime is HawkesRegime.CASCADE
    assert cascade.quotes_pulled
    assert controller.adjust_quotes(_quotes(), market_mid=100.0, decision=cascade).bid is None

    during = controller.evaluate(1_500)
    assert during.cooldown_active
    assert during.quotes_pulled
    recovered = controller.evaluate(10_000)
    assert recovered.regime is HawkesRegime.QUIET
    assert not recovered.quotes_pulled


def test_risk_manager_emits_hawkes_block_and_reduce_only_unwind_plan():
    hawkes = _controller()
    manager = MarketMakingRiskManager(
        MarketMakingRiskConfig(
            max_position=1.0,
            max_order_qty=0.1,
            max_drawdown=100.0,
            hawkes=hawkes.config,
            unwind=UnwindConfig(horizon_ms=3_000, interval_ms=1_000),
        ),
        hawkes_controller=hawkes,
    )
    for trade_id, timestamp, numeric_id in (
        ("one", 1_000, 1),
        ("two", 1_010, 2),
        ("three", 1_020, 3),
    ):
        manager.on_trade(
            TradeTick(timestamp, numeric_id, 100.0, 0.1, True, "BTCUSDT")
        )
    permitted = manager.apply_quotes(_quotes(), position=0.1, timestamp=1_020, market_mid=100.0)
    assert permitted.bid is None and permitted.ask is None
    assert manager.unwind_plan is not None
    assert all(item.reduce_only for item in manager.unwind_plan.slices)
    assert any(event["event_type"] == "UNWIND_PLAN" for event in manager.events)
