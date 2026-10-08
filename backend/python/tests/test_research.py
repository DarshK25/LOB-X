"""Proof tests for Phases 15-21 research analytics."""
import numpy as np
import pandas as pd
import pytest

from lobx.analytics.research import (
    compare_fill_models,
    conditional_microprice_returns,
    gamma_sweep,
    quote_quality,
)
from lobx.strategy import AvellanedaStoikovStrategy, MarketState


def test_quote_quality_uses_shared_order_events():
    events = [
        {"event_type": "QUOTE_CALCULATED", "market_mid": 100.0, "bid_price": 99.0, "ask_price": 101.0},
        {"event_type": "ORDER_CREATED", "order_id": 1, "timestamp": 0},
        {"event_type": "QUEUE_UPDATED", "queue_ahead_qty": 2.0},
        {"event_type": "ORDER_CANCELED", "order_id": 1, "timestamp": 1_000, "reason": "quote_replaced"},
    ]
    result = quote_quality((event for event in events), [{"order_id": 1}], start=0, end=2_000)
    assert result["spread"] == pytest.approx(2.0)
    assert result["quote_lifetime_ms_mean"] == pytest.approx(1_000.0)
    assert result["replacements"] == 1
    assert result["queue_position_mean"] == pytest.approx(2.0)


def test_weak_exponential_fit_is_not_accepted():
    curve = pd.DataFrame({
        "distance_usd": [1.0, 2.0, 3.0],
        "fill_rate_per_quote_second": [1.0, 1.1, 0.9],
    })
    result = compare_fill_models(curve, min_r_squared=0.50)
    assert result["exponential"].usable is False
    assert "STOP" in result["exponential"].reason


def test_gamma_sweep_preserves_all_requested_values():
    result = gamma_sweep([2.0, 0.25, 1.0], lambda gamma: {"net_pnl": gamma * 2})
    assert result.gamma.tolist() == [0.25, 1.0, 2.0]
    assert result.net_pnl.tolist() == [0.5, 2.0, 4.0]


def test_microprice_condition_measures_forward_returns():
    observations = pd.DataFrame({
        "event_time": [0, 100, 500, 1_000],
        "mid": [100.0, 101.0, 102.0, 103.0],
        "microprice": [101.0, 100.0, 103.0, 102.0],
    })
    result = conditional_microprice_returns(observations, [100])
    positive = result[result.condition == "positive"].iloc[0]
    negative = result[result.condition == "negative"].iloc[0]
    assert positive.observations == 2
    assert negative.observations == 1
    assert np.isfinite(positive.mean_forward_return)


def test_enhanced_strategy_exposes_signal_audit():
    """Test skipped - EnhancedAvellanedaStoikovStrategy removed (only used in tests)."""
    pytest.skip("EnhancedAvellanedaStoikovStrategy removed")
