"""tests/test_inventory.py — unit tests for InventoryTracker."""
import pytest
from lobx.execution.inventory import InventoryTracker


def test_initial_state():
    inv = InventoryTracker()
    assert inv.position        == 0.0
    assert inv.avg_entry_price == 0.0
    assert inv.realized_pnl    == 0.0


def test_single_buy_opens_long():
    inv = InventoryTracker()
    inv.on_fill("buy", 100.0, 1.0)
    assert inv.position        == 1.0
    assert inv.avg_entry_price == 100.0
    assert inv.realized_pnl    == 0.0


def test_buy_then_buy_blends_avg_cost():
    inv = InventoryTracker()
    inv.on_fill("buy", 100.0, 1.0)
    inv.on_fill("buy", 102.0, 1.0)
    assert inv.position == 2.0
    assert abs(inv.avg_entry_price - 101.0) < 1e-9
    assert inv.realized_pnl == 0.0


def test_sell_closes_long_realizes_pnl():
    inv = InventoryTracker()
    inv.on_fill("buy",  100.0, 1.0)
    inv.on_fill("sell", 105.0, 1.0)
    assert abs(inv.position)     < 1e-12
    assert abs(inv.realized_pnl - 5.0) < 1e-9


def test_partial_close_realizes_partial_pnl():
    inv = InventoryTracker()
    inv.on_fill("buy",  100.0, 2.0)
    inv.on_fill("sell", 110.0, 1.0)
    assert abs(inv.position - 1.0)     < 1e-9
    assert abs(inv.realized_pnl - 10.0) < 1e-9
    assert abs(inv.avg_entry_price - 100.0) < 1e-9


def test_flip_through_zero():
    """Sell 2 when long 1 → realize profit on 1, open short 1."""
    inv = InventoryTracker()
    inv.on_fill("buy",  100.0, 1.0)
    inv.on_fill("sell", 110.0, 2.0)
    assert abs(inv.position - (-1.0))     < 1e-9
    assert abs(inv.realized_pnl - 10.0)   < 1e-9   # profit on the 1 closed
    assert abs(inv.avg_entry_price - 110.0) < 1e-9  # short opened at 110


def test_short_realized_pnl():
    """Short then buy back at lower price → profit."""
    inv = InventoryTracker()
    inv.on_fill("sell", 200.0, 1.0)
    inv.on_fill("buy",  190.0, 1.0)
    assert abs(inv.position)              < 1e-12
    assert abs(inv.realized_pnl - 10.0)  < 1e-9


def test_unrealized_pnl_long():
    inv = InventoryTracker()
    inv.on_fill("buy", 100.0, 1.0)
    assert abs(inv.unrealized_pnl(110.0) - 10.0) < 1e-9
    assert abs(inv.unrealized_pnl(95.0)  - (-5.0)) < 1e-9


def test_total_pnl():
    inv = InventoryTracker()
    inv.on_fill("buy",  100.0, 1.0)
    inv.on_fill("sell", 105.0, 0.5)  # realize 2.50
    # Remaining 0.5 BTC long, mark at 108 → unrealized = 0.5*(108-100) = 4.0
    assert abs(inv.realized_pnl - 2.5)    < 1e-9
    assert abs(inv.unrealized_pnl(108.0) - 4.0) < 1e-9
    assert abs(inv.total_pnl(108.0) - 6.5) < 1e-9


def test_invalid_qty_raises():
    inv = InventoryTracker()
    with pytest.raises(ValueError, match="positive"):
        inv.on_fill("buy", 100.0, 0.0)


def test_invalid_side_raises():
    inv = InventoryTracker()
    with pytest.raises(ValueError, match="buy.*sell"):
        inv.on_fill("long", 100.0, 1.0)


def test_reset_clears_state():
    inv = InventoryTracker()
    inv.on_fill("buy", 100.0, 1.0)
    inv.on_fill("sell", 105.0, 1.0)
    inv.reset()
    assert inv.position        == 0.0
    assert inv.avg_entry_price == 0.0
    assert inv.realized_pnl    == 0.0
