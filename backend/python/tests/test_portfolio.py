"""Proof tests for canonical portfolio accounting and Phase 13 metrics."""
import pytest

from lobx.execution.inventory import InventoryTracker
from lobx.execution.portfolio import PortfolioEngine
from lobx.risk import MarketMakingRiskConfig, MarketMakingRiskManager
from lobx.strategy.base import Quote, QuotePair


def test_portfolio_reports_cash_pnl_and_fees_separately():
    portfolio = PortfolioEngine(initial_capital=1_000.0)
    portfolio.on_fill("buy", 100.0, 1.0, fee=0.10)
    snapshot = portfolio.snapshot(105.0)
    assert snapshot["cash"] == pytest.approx(900.0)
    assert snapshot["inventory"] == pytest.approx(1.0)
    assert snapshot["realized_pnl"] == pytest.approx(0.0)
    assert snapshot["unrealized_pnl"] == pytest.approx(5.0)
    assert snapshot["fees"] == pytest.approx(0.10)
    assert snapshot["gross_pnl"] == pytest.approx(5.0)
    assert snapshot["net_pnl"] == pytest.approx(4.90)


def test_inventory_statistics_are_time_weighted():
    inventory = InventoryTracker()
    inventory.observe(0)
    inventory.on_fill("buy", 100.0, 1.0)
    inventory.observe(1_000)
    inventory.on_fill("sell", 100.0, 1.0)
    inventory.observe(2_000)
    assert inventory.max_inventory == pytest.approx(1.0)
    assert inventory.average_inventory == pytest.approx(0.5)
    assert inventory.inventory_variance == pytest.approx(0.25)
    assert inventory.time_spent_long_ms == 1_000
    assert inventory.time_spent_short_ms == 0
    assert inventory.inventory_half_life_ms == 1_000
    assert len(inventory.inventory_distribution) == 3


def test_risk_rejection_is_explicit():
    manager = MarketMakingRiskManager(
        MarketMakingRiskConfig(max_position=0.005, max_order_qty=0.001)
    )
    result = manager.apply_quotes(
        QuotePair(bid=Quote(100.0, 0.001), ask=None),
        position=0.0045,
        timestamp=10,
        market_mid=100.0,
    )
    assert result.bid is None
    assert manager.events[-1]["event_type"] == "RISK_BLOCK"
    assert manager.events[-1]["reason"] == "MAX_POSITION"
    assert manager.events[-1]["allowed"] == pytest.approx(0.0005)
