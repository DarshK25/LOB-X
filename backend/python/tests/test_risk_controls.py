from lobx.risk import MarketMakingRiskConfig, MarketMakingRiskManager
from lobx.strategy.base import Quote, QuotePair


def _quotes() -> QuotePair:
    return QuotePair(bid=Quote(99.0, 0.001), ask=Quote(101.0, 0.001))


def test_position_limit_only_suppresses_risk_increasing_side():
    manager = MarketMakingRiskManager(
        MarketMakingRiskConfig(max_position=0.001, max_order_qty=0.001, max_drawdown=10.0)
    )
    permitted = manager.apply_quotes(_quotes(), position=0.001)
    assert permitted.bid is None
    assert permitted.ask is not None


def test_drawdown_halts_both_sides_for_rest_of_session():
    manager = MarketMakingRiskManager(
        MarketMakingRiskConfig(max_position=0.01, max_order_qty=0.001, max_drawdown=1.0)
    )
    manager.update_equity(-1.0)
    permitted = manager.apply_quotes(_quotes(), position=0.0)
    assert manager.halted
    assert manager.halt_reason == "max_drawdown"
    assert permitted.bid is None and permitted.ask is None
