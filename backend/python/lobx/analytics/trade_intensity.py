"""
analytics/trade_intensity.py — Trade arrival intensity estimation.

Reference
---------
Standard Poisson process intensity estimation for high-frequency trading.
Used in Avellaneda & Stoikov (2008) to model the arrival of market orders.

Intensity lambda(t) is typically estimated as N(t) / delta_t, where
N(t) is the number of trades (or traded volume) in time window delta_t.
"""
from __future__ import annotations

import time
from collections import deque


class TradeIntensityEstimator:
    """Estimates the arrival rate of trades (intensity) using a rolling time window.
    
    Tracks buyer-initiated and seller-initiated trades separately.
    """

    def __init__(self, window_seconds: float = 60.0) -> None:
        self.window_seconds = window_seconds
        # Store tuples of (timestamp, is_buyer_maker, qty)
        self._trades: deque[tuple[float, bool, float]] = deque()
        
    def update(self, is_buyer_maker: bool, qty: float, timestamp: float | None = None) -> None:
        """Record a new trade."""
        if timestamp is None:
            timestamp = time.time()
            
        self._trades.append((timestamp, is_buyer_maker, qty))
        self._evict_old(timestamp)

    def _evict_old(self, current_time: float) -> None:
        """Remove trades outside the time window."""
        cutoff = current_time - self.window_seconds
        while self._trades and self._trades[0][0] < cutoff:
            self._trades.popleft()

    @property
    def arrival_rate(self) -> float:
        """Total trades per second in the window."""
        return len(self._trades) / self.window_seconds
        
    @property
    def buy_arrival_rate(self) -> float:
        """Buyer-initiated (is_buyer_maker=False) trades per second."""
        buys = sum(1 for _, is_bm, _ in self._trades if not is_bm)
        return buys / self.window_seconds
        
    @property
    def sell_arrival_rate(self) -> float:
        """Seller-initiated (is_buyer_maker=True) trades per second."""
        sells = sum(1 for _, is_bm, _ in self._trades if is_bm)
        return sells / self.window_seconds

    @property
    def volume_intensity(self) -> float:
        """Total volume traded per second."""
        total_qty = sum(qty for _, _, qty in self._trades)
        return total_qty / self.window_seconds
