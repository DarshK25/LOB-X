"""
analytics/imbalance.py — Order Book Imbalance (OBI).

Reference
---------
Cartea, Jaimungal, and Penalva (2015) "Algorithmic and High-Frequency Trading"
Section 3.1: Limit Order Book Features.

Order Book Imbalance (OBI) is a normalized measure of the volume imbalance
between the bid and ask sides. Typically computed at the top level (L1),
but can be extended to deeper levels.

OBI = (BidVolume - AskVolume) / (BidVolume + AskVolume)

Range is [-1, 1].
+1 means entirely bid volume (strong buying pressure)
-1 means entirely ask volume (strong selling pressure)
"""
from __future__ import annotations

import lobx_cpp


def compute_obi(book: lobx_cpp.OrderBook) -> float:
    """Compute the L1 Order Book Imbalance (OBI).

    Returns
    -------
    float: Imbalance in [-1.0, 1.0]. Returns 0.0 if book is empty.
    """
    bid_price = book.best_bid()
    ask_price = book.best_ask()
    
    if bid_price is None or ask_price is None:
        return 0.0
        
    bid_qty = book.best_bid_qty() or 0.0
    ask_qty = book.best_ask_qty() or 0.0
    
    total_qty = bid_qty + ask_qty
    if total_qty == 0.0:
        return 0.0
        
    return (bid_qty - ask_qty) / total_qty
    
    
class ImbalanceMonitor:
    """Tracks Order Book Imbalance over time."""
    
    def __init__(self) -> None:
        self.current_obi: float = 0.0
        
    def update(self, book: lobx_cpp.OrderBook) -> None:
        self.current_obi = compute_obi(book)
