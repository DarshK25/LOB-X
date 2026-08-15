"""
analytics/microprice.py — Microprice computation.

Reference
---------
Stoikov (2018) "The Micro-Price: A High-Frequency Estimator of Future Prices"

Microprice adjusts the mid-price by weighting the best bid and best ask
using the opposite side's volume. If there is more volume on the bid side,
the microprice will be closer to the ask (predicting an upward move).

Microprice = (Ask * BidVolume + Bid * AskVolume) / (BidVolume + AskVolume)
"""
from __future__ import annotations

import lobx_cpp


def compute_microprice(book: lobx_cpp.OrderBook) -> float | None:
    """Compute the microprice from the current state of the order book.

    Returns
    -------
    float or None: The microprice, or None if the book is empty on either side.
    """
    bid_price = book.best_bid()
    ask_price = book.best_ask()

    if bid_price is None or ask_price is None:
        return None
        
    bid_qty = book.best_bid_qty() or 0.0
    ask_qty = book.best_ask_qty() or 0.0
    
    total_qty = bid_qty + ask_qty
    if total_qty == 0.0:
        # Fallback to mid-price if qty is somehow 0 (should not happen in valid book)
        return (bid_price + ask_price) / 2.0
        
    # Weight by opposite side volume
    return (ask_price * bid_qty + bid_price * ask_qty) / total_qty


class MicropriceMonitor:
    """Tracks time-series of microprice and its deviation from mid-price."""

    def __init__(self) -> None:
        self.current_microprice: float | None = None
        self.current_mid: float | None = None

    def update(self, book: lobx_cpp.OrderBook) -> None:
        """Update current values from the order book."""
        bid = book.best_bid()
        ask = book.best_ask()
        
        if bid is None or ask is None:
            self.current_microprice = None
            self.current_mid = None
            return
            
        self.current_microprice = compute_microprice(book)
        self.current_mid = (bid + ask) / 2.0

    @property
    def drift(self) -> float | None:
        """Microprice drift: Microprice - MidPrice.
        
        Positive drift suggests upward price pressure.
        Negative drift suggests downward price pressure.
        """
        if self.current_microprice is not None and self.current_mid is not None:
            return self.current_microprice - self.current_mid
        return None
