"""Unit tests for Order Flow Imbalance (OFI)."""
import pytest
import lobx_cpp
from lobx.analytics.order_flow import OrderFlowImbalance


def test_ofi_synthetic_updates():
    book = lobx_cpp.OrderBook()
    ofi = OrderFlowImbalance(window=5)
    
    # Tick 1: Initial state
    book.add_limit_order(lobx_cpp.Order(1, 100, 10, True))
    book.add_limit_order(lobx_cpp.Order(2, 102, 10, False))
    assert ofi.update(book) == 0.0
    
    # Tick 2: Bid volume added at same price (bid_flow = +20, ask_flow = 0) -> OFI = +20
    book.add_limit_order(lobx_cpp.Order(3, 100, 20, True))
    assert ofi.update(book) == pytest.approx(20.0)
    
    # Tick 3: Ask volume added at same price (bid_flow = 0, ask_flow = +15) -> OFI = -15
    book.add_limit_order(lobx_cpp.Order(4, 102, 15, False))
    assert ofi.update(book) == pytest.approx(-15.0)
    
    # Tick 4: Price movement UP
    # Bid price increases -> bid_flow = new_qty (+50)
    # Ask price increases -> ask_flow = -prev_qty (-25)
    # OFI = bid_flow - ask_flow = 50 - (-25) = +75
    book.add_limit_order(lobx_cpp.Order(5, 101, 50, True))
    book.add_limit_order(lobx_cpp.Order(6, 103, 30, False))
    book.cancel(2)
    book.cancel(4)
    assert ofi.update(book) == pytest.approx(75.0)
    
    # Tick 5: Price movement DOWN
    # Bid price decreases -> bid_flow = -prev_qty (-50)
    # Ask price decreases -> ask_flow = new_qty (+20)
    # OFI = bid_flow - ask_flow = -50 - 20 = -70
    book.cancel(5)
    book.add_limit_order(lobx_cpp.Order(7, 101, 20, False))
    assert ofi.update(book) == pytest.approx(-70.0)
    
    # Check window sums
    # History: [0.0, 20.0, -15.0, 75.0, -70.0]
    assert ofi.current == pytest.approx(-70.0)
    assert ofi.window_sum == pytest.approx(10.0) # 0 + 20 - 15 + 75 - 70
    
    # Tick 6: Push out oldest tick (0.0 leaves, new tick comes in)
    book.add_limit_order(lobx_cpp.Order(8, 101, 15, True))
    assert ofi.update(book) == pytest.approx(15.0)
    
    # New History: [20.0, -15.0, 75.0, -70.0, 15.0]
    assert ofi.window_sum == pytest.approx(25.0)
