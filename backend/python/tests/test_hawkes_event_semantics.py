"""Test that Hawkes processes trade arrivals, not depth updates.

This test documents the fix for the bug where state.timestamp (depth event time)
was being appended to _recent_trade_times_ms in AvellanedaStoikovStrategy.

Hawkes trade intensity MUST track actual trade events, not order book updates.
"""
import pytest
from lobx.strategy.base import MarketState, Quote
from lobx.strategy.avellaneda import AvellanedaStoikovStrategy


def test_hawkes_ignores_depth_updates_without_trades():
    """Depth updates without trades should NOT increase Hawkes trade history."""
    strategy = AvellanedaStoikovStrategy(
        gamma=0.1,
        kappa=1.5,
        horizon_seconds=300.0,
        qty=0.001,
        use_hawkes=True,
    )
    
    initial_intensity = strategy._hawkes.intensity if strategy._hawkes else None
    
    # Process 5 depth updates with NO trade information
    for i in range(5):
        state = MarketState.from_book(
            timestamp=1000 + i * 100,  # depth event timestamps
            best_bid=99.0,
            best_bid_qty=10.0,
            best_ask=101.0,
            best_ask_qty=10.0,
            volatility=0.1,
            inventory=0.0,
            # NO trade fields populated
            trade_price=None,
            trade_quantity=None,
            trade_side=None,
        )
        strategy.on_tick(state)
    
    # Hawkes intensity should remain at baseline (no trades observed)
    assert strategy._hawkes is not None
    current_intensity = strategy._hawkes.intensity
    assert abs(current_intensity - initial_intensity) < 1e-6, \
        f"Depth updates without trades should not change intensity (was {initial_intensity}, now {current_intensity})"


def test_hawkes_tracks_actual_trade_events():
    """Trade events should populate Hawkes intensity and increase it."""
    strategy = AvellanedaStoikovStrategy(
        gamma=0.1,
        kappa=1.5,
        horizon_seconds=300.0,
        qty=0.001,
        use_hawkes=True,
        hawkes_mu=1.0,
        hawkes_alpha=1.5,
        hawkes_beta=2.0,
    )
    
    assert strategy._hawkes is not None
    initial_intensity = strategy._hawkes.intensity  # Should be mu = 1.0
    
    # Process states with actual trades
    trade_count = 0
    for i in range(3):
        ts = 1000 + i * 250
        state = MarketState.from_book(
            timestamp=ts,
            best_bid=99.0,
            best_bid_qty=10.0,
            best_ask=101.0,
            best_ask_qty=10.0,
            volatility=0.1,
            inventory=0.0,
            # Trade information present
            trade_price=100.0,
            trade_quantity=0.5,
            trade_side="buy",
        )
        strategy.on_tick(state)
        trade_count += 1
    
    # Hawkes intensity should have increased due to excitation
    final_intensity = strategy._hawkes.intensity
    assert final_intensity > initial_intensity, \
        f"Trade events should increase Hawkes intensity (was {initial_intensity:.4f}, now {final_intensity:.4f})"


def test_hawkes_mixed_depth_and_trade_events():
    """Mixed sequence: depth updates and trades should only excite on trades."""
    strategy = AvellanedaStoikovStrategy(
        gamma=0.1,
        kappa=1.5,
        horizon_seconds=300.0,
        qty=0.001,
        use_hawkes=True,
        hawkes_mu=1.0,
        hawkes_alpha=1.5,
        hawkes_beta=2.0,
    )
    
    assert strategy._hawkes is not None
    intensities_after_trades = []
    
    # Sequence: depth, depth, TRADE, depth, TRADE, depth
    events = [
        (1000, None),     # depth only
        (1100, None),     # depth only
        (1200, 100.0),    # depth + trade
        (1300, None),     # depth only
        (1400, 100.5),    # depth + trade
        (1500, None),     # depth only
    ]
    
    for ts, trade_price in events:
        state = MarketState.from_book(
            timestamp=ts,
            best_bid=99.0,
            best_bid_qty=10.0,
            best_ask=101.0,
            best_ask_qty=10.0,
            volatility=0.1,
            inventory=0.0,
            trade_price=trade_price,
            trade_quantity=0.5 if trade_price else None,
            trade_side="buy" if trade_price else None,
        )
        strategy.on_tick(state)
        if trade_price is not None:
            intensities_after_trades.append(strategy._hawkes.intensity)
    
    # Should have recorded 2 trade events
    assert len(intensities_after_trades) == 2, \
        f"Expected 2 trade events, got {len(intensities_after_trades)}"
    # Intensity after first trade should be higher than baseline
    assert intensities_after_trades[0] > 1.0, \
        "First trade should excite intensity above baseline"


def test_hawkes_disabled_does_not_track():
    """With use_hawkes=False, no Hawkes instance should be created."""
    strategy = AvellanedaStoikovStrategy(
        gamma=0.1,
        kappa=1.5,
        horizon_seconds=300.0,
        qty=0.001,
        use_hawkes=False,  # Disabled
    )
    
    # Should not have Hawkes instance
    assert strategy._hawkes is None, \
        "Hawkes instance should not be created when use_hawkes=False"
    
    # Process states with trades
    for ts in [1000, 1100, 1200]:
        state = MarketState.from_book(
            timestamp=ts,
            best_bid=99.0,
            best_bid_qty=10.0,
            best_ask=101.0,
            best_ask_qty=10.0,
            volatility=0.1,
            inventory=0.0,
            trade_price=100.0,
            trade_quantity=0.5,
            trade_side="buy",
        )
        quotes = strategy.on_tick(state)
        assert quotes.bid is not None and quotes.ask is not None, \
            "Strategy should still produce quotes when Hawkes is disabled"
    
    # Verify still None
    assert strategy._hawkes is None, \
        "Hawkes should remain None throughout"
