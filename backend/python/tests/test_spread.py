"""Unit tests for Spread analytics."""
import pytest
import lobx_cpp
from lobx.analytics.spread import SpreadMonitor


def test_spread_monitor():
    book = lobx_cpp.OrderBook()
    monitor = SpreadMonitor(window=3)
    
    assert monitor.current is None
    assert monitor.mean is None
    
    # Add bids and asks to create a spread
    book.add_limit_order(lobx_cpp.Order(1, 100, 1, True))
    book.add_limit_order(lobx_cpp.Order(2, 102, 1, False))
    
    monitor.update(book)
    assert monitor.current == pytest.approx(2.0)
    assert monitor.mean == pytest.approx(2.0)
    
    # Narrow the spread
    book.add_limit_order(lobx_cpp.Order(3, 101, 1, True))
    monitor.update(book)
    assert monitor.current == pytest.approx(1.0)
    assert monitor.mean == pytest.approx(1.5) # (2.0 + 1.0) / 2
    
    # Widen again
    book.cancel(3)
    monitor.update(book)
    assert monitor.current == pytest.approx(2.0)
    assert monitor.mean == pytest.approx(5.0 / 3.0) # (2.0 + 1.0 + 2.0) / 3
    
    # Push out of window
    monitor.update(book)
    assert monitor.current == pytest.approx(2.0)
    assert monitor.mean == pytest.approx(5.0 / 3.0) # (1.0 + 2.0 + 2.0) / 3
