"""Unit tests for Trade Intensity."""
import pytest
from lobx.analytics.trade_intensity import TradeIntensityEstimator


def test_trade_intensity():
    # Window of 10 seconds
    estimator = TradeIntensityEstimator(window_seconds=10.0)
    
    # No trades
    assert estimator.arrival_rate == 0.0
    
    # Add trades
    estimator.update(is_buyer_maker=False, qty=1.0, timestamp=100.0)
    estimator.update(is_buyer_maker=False, qty=1.5, timestamp=101.0)
    estimator.update(is_buyer_maker=True,  qty=2.0, timestamp=105.0)
    
    # Total trades = 3 in 10s window = 0.3/s
    assert estimator.arrival_rate == pytest.approx(0.3)
    
    # Buy trades (buyer_maker=False) = 2 in 10s = 0.2/s
    assert estimator.buy_arrival_rate == pytest.approx(0.2)
    
    # Sell trades (buyer_maker=True) = 1 in 10s = 0.1/s
    assert estimator.sell_arrival_rate == pytest.approx(0.1)
    
    # Volume intensity = (1.0 + 1.5 + 2.0) / 10 = 0.45/s
    assert estimator.volume_intensity == pytest.approx(0.45)
    
    # Move time forward to evict oldest trade (timestamp 100.0)
    # Current time = 110.1, cutoff = 100.1
    estimator.update(is_buyer_maker=True, qty=1.0, timestamp=110.1)
    
    # Trades now in window: (101.0), (105.0), (110.1) -> length 3
    assert estimator.arrival_rate == pytest.approx(0.3)
    # Buy trades: (101.0) -> length 1
    assert estimator.buy_arrival_rate == pytest.approx(0.1)
