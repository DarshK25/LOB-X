"""
execution/inventory.py — average-cost-basis position tracker.

Tracks: position (signed), average entry price, realized PnL.

Observable invariant for AS verification:
  After a shadow fill, the NEXT on_tick() should produce a reservation
  price that has shifted AWAY from mid in the direction that would reduce
  inventory.  If it stays symmetric around mid, the inventory value being
  passed into MarketState is wrong (probably still 0.0).

Why average-cost and not FIFO/LIFO?
  For a market-making strategy, average-cost is the correct mental model:
  you're continuously adding to and reducing a position, not matching
  specific lots.  The accounting question ("which lot was sold?") doesn't
  change the realized PnL — only matters for tax purposes, not for risk.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class InventoryTracker:
    """Average-cost inventory tracker with realized PnL.

    Attributes (read-only in normal use)
    -------------------------------------
    position        : signed current holding (+ = long BTC, - = short BTC)
    avg_entry_price : average cost basis for the current position
    realized_pnl    : total realized PnL since tracker was created
    """

    position:        float = 0.0
    avg_entry_price: float = 0.0
    realized_pnl:    float = 0.0

    # Internal: count fills for debugging / logging
    _fill_count: int = field(default=0, init=False, repr=False)

    def on_fill(self, side: str, price: float, qty: float) -> None:
        """Update position and PnL from one fill.

        Parameters
        ----------
        side  : "buy" or "sell"
        price : fill price (float USD)
        qty   : absolute fill quantity (always positive)

        Handles three cases:
          1. Adding to existing position (same sign) → blend avg cost.
          2. Partially closing existing position (opposite sign, smaller qty)
             → realize partial PnL.
          3. Flipping through zero (opposite sign, larger qty) → realize full
             position PnL and open a new short/long at the fill price.
        """
        if qty <= 0:
            raise ValueError(f"fill qty must be positive, got {qty}")
        if side not in ("buy", "sell"):
            raise ValueError(f"side must be 'buy' or 'sell', got {side!r}")

        signed_qty = qty if side == "buy" else -qty
        self._fill_count += 1

        if self.position == 0.0:
            # No existing position — open new.
            self.position        = signed_qty
            self.avg_entry_price = price
            return

        same_direction = (self.position > 0) == (signed_qty > 0)

        if same_direction:
            # Adding to position — blend average cost.
            total_value          = self.avg_entry_price * abs(self.position) + price * qty
            self.position       += signed_qty
            self.avg_entry_price = total_value / abs(self.position)

        else:
            # Closing (full or partial) or flipping.
            closing_qty = min(abs(signed_qty), abs(self.position))

            # Realize PnL on the closing portion.
            if self.position > 0:
                self.realized_pnl += (price - self.avg_entry_price) * closing_qty
            else:
                self.realized_pnl += (self.avg_entry_price - price) * closing_qty

            remaining_qty = abs(signed_qty) - closing_qty
            self.position += signed_qty

            if abs(self.position) < 1e-12:
                # Fully closed — clear avg cost.
                self.position        = 0.0
                self.avg_entry_price = 0.0
            elif remaining_qty > 1e-12:
                # Flipped through zero — new position opens at fill price.
                self.avg_entry_price = price

    def unrealized_pnl(self, mark_price: float) -> float:
        """Mark-to-market unrealized PnL at the given price.

        Parameters
        ----------
        mark_price : current mid-price (or last trade price)
        """
        return (mark_price - self.avg_entry_price) * self.position

    def total_pnl(self, mark_price: float) -> float:
        """Total PnL = realized + unrealized."""
        return self.realized_pnl + self.unrealized_pnl(mark_price)

    def reset(self) -> None:
        """Zero out all state — for backtester replay resets."""
        self.position        = 0.0
        self.avg_entry_price = 0.0
        self.realized_pnl    = 0.0
        self._fill_count     = 0
