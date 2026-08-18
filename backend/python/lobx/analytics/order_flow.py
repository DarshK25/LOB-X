"""
analytics/order_flow.py — Order Flow Imbalance (OFI).

Reference
---------
Cont, Kukanov & Stoikov (2014) "The Price Impact of Order Book Events"
Journal of Financial Econometrics.

OFI measures the net buying/selling pressure at the best bid and ask over
a time interval. It accounts for price changes, volume additions, and cancellations.

Let e_t be the event at time t.
If bid_price_t > bid_price_{t-1}: bid_flow = bid_qty_t
If bid_price_t = bid_price_{t-1}: bid_flow = bid_qty_t - bid_qty_{t-1}
If bid_price_t < bid_price_{t-1}: bid_flow = -bid_qty_{t-1}

(Symmetric logic applies to the ask side, where ask_flow is positive for selling pressure).
OFI_t = bid_flow_t - ask_flow_t
"""
from __future__ import annotations

import lobx_cpp
from collections import deque


class OrderFlowImbalance:
    """Tracks Order Flow Imbalance (OFI) over updates."""

    def __init__(self, window: int = 100) -> None:
        self._window = window
        self._ofi_history: deque[float] = deque(maxlen=window)
        
        # State from previous update
        self._prev_bid_price: float | None = None
        self._prev_bid_qty: float = 0.0
        self._prev_ask_price: float | None = None
        self._prev_ask_qty: float = 0.0
        
        # Cumulative OFI
        self.cumulative_ofi: float = 0.0

    def update(self, book: lobx_cpp.OrderBook) -> float:
        """Update state with the current book and return the instantaneous OFI."""
        curr_bid_price = book.best_bid()
        curr_ask_price = book.best_ask()
        
        if curr_bid_price is None or curr_ask_price is None:
            return 0.0
            
        curr_bid_qty = book.best_bid_qty() or 0.0
        curr_ask_qty = book.best_ask_qty() or 0.0
        
        bid_flow = 0.0
        ask_flow = 0.0
        
        # Calculate bid flow
        if self._prev_bid_price is not None:
            if curr_bid_price > self._prev_bid_price:
                bid_flow = curr_bid_qty
            elif curr_bid_price == self._prev_bid_price:
                bid_flow = curr_bid_qty - self._prev_bid_qty
            else:
                bid_flow = -self._prev_bid_qty
                
        # Calculate ask flow
        if self._prev_ask_price is not None:
            if curr_ask_price < self._prev_ask_price:
                ask_flow = curr_ask_qty
            elif curr_ask_price == self._prev_ask_price:
                ask_flow = curr_ask_qty - self._prev_ask_qty
            else:
                ask_flow = -self._prev_ask_qty
                
        # Update state for next tick
        self._prev_bid_price = curr_bid_price
        self._prev_bid_qty = curr_bid_qty
        self._prev_ask_price = curr_ask_price
        self._prev_ask_qty = curr_ask_qty
        
        # Instantaneous OFI
        # If no previous state, OFI is 0 for this tick
        ofi = 0.0
        if self._prev_bid_price is not None and self._prev_ask_price is not None:
             ofi = bid_flow - ask_flow
             
        self._ofi_history.append(ofi)
        self.cumulative_ofi += ofi
        
        return ofi

    @property
    def current(self) -> float:
        """Most recent instantaneous OFI."""
        return self._ofi_history[-1] if self._ofi_history else 0.0
        
    @property
    def window_sum(self) -> float:
        """Sum of OFI over the tracking window."""
        return sum(self._ofi_history)
