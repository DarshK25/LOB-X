"""Unit tests for Microprice analytics."""
import pytest
import lobx_cpp
from lobx.analytics.microprice import compute_microprice, MicropriceMonitor


def test_compute_microprice():
    book = lobx_cpp.OrderBook()
    
    # Empty book
    assert compute_microprice(book) is None
    
    # Only one side
    book.add_limit_order(lobx_cpp.Order(1, 100, 1, True))
    assert compute_microprice(book) is None
    
    # Symmetric book -> microprice = mid price
    book.add_limit_order(lobx_cpp.Order(2, 102, 1, False))
    assert compute_microprice(book) == pytest.approx(101.0)
    
    # Imbalanced book: More volume on bid -> microprice closer to ask (102.0)
    book.add_limit_order(lobx_cpp.Order(3, 100, 3, True)) # Total bid qty = 4.0, Total ask qty = 1.0
    # MP = (102 * 4 + 100 * 1) / 5 = (408 + 100) / 5 = 508 / 5 = 101.6
    assert compute_microprice(book) == pytest.approx(101.6)
    
    # Imbalanced book: More volume on ask -> microprice closer to bid (100.0)
    book.add_limit_order(lobx_cpp.Order(4, 102, 9, False)) # Total bid qty = 4.0, Total ask qty = 10.0
    # MP = (102 * 4 + 100 * 10) / 14 = (408 + 1000) / 14 = 1408 / 14 = 100.5714...
    assert compute_microprice(book) == pytest.approx(1408.0 / 14.0)


def test_microprice_monitor():
    book = lobx_cpp.OrderBook()
    monitor = MicropriceMonitor()
    
    assert monitor.drift is None
    
    book.add_limit_order(lobx_cpp.Order(1, 100, 4, True))
    book.add_limit_order(lobx_cpp.Order(2, 102, 1, False))
    
    monitor.update(book)
    assert monitor.current_mid == pytest.approx(101.0)
    assert monitor.current_microprice == pytest.approx(101.6)
    assert monitor.drift == pytest.approx(0.6) # positive drift -> upward pressure
