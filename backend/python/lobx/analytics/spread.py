"""Spread analytics."""
from __future__ import annotations
from collections import deque
import lobx_cpp


class SpreadMonitor:
    """Rolling live spread monitor retained for the Phase 3 public API."""

    def __init__(self, window: int = 100) -> None:
        if window <= 0:
            raise ValueError("window must be positive")
        self._values: deque[float] = deque(maxlen=window)

    @property
    def current(self) -> float | None:
        return self._values[-1] if self._values else None

    @property
    def mean(self) -> float | None:
        return sum(self._values) / len(self._values) if self._values else None

    def update(self, book: lobx_cpp.OrderBook) -> float | None:
        bid = book.best_bid()
        ask = book.best_ask()
        # Support both the legacy (price, qty) binding and the current
        # scalar-price binding documented in the decisions log.
        if isinstance(bid, tuple):
            bid = bid[0]
        if isinstance(ask, tuple):
            ask = ask[0]
        if bid is None or ask is None:
            return None
        value = float(ask) - float(bid)
        self._values.append(value)
        return value


class SpreadAnalyzer:
    """Offline analyzer for spread data."""
    
    def __init__(self, spreads: list[float]) -> None:
        self.spreads = [s for s in spreads if s is not None and s > 0]
        
    def average_spread(self) -> float:
        if not self.spreads:
            return 0.0
        return sum(self.spreads) / len(self.spreads)
        
    def median_spread(self) -> float:
        if not self.spreads:
            return 0.0
        sorted_s = sorted(self.spreads)
        n = len(sorted_s)
        if n % 2 == 0:
            return (sorted_s[n//2 - 1] + sorted_s[n//2]) / 2.0
        return sorted_s[n//2]
        
    def spread_distribution(self) -> dict[str, float]:
        """Returns min, max, mean, median, and 95th percentile."""
        if not self.spreads:
            return {}
        sorted_s = sorted(self.spreads)
        n = len(sorted_s)
        p95_idx = int(0.95 * n)
        return {
            "min": sorted_s[0],
            "max": sorted_s[-1],
            "mean": self.average_spread(),
            "median": self.median_spread(),
            "p95": sorted_s[p95_idx]
        }
        
    def spread_histogram(self, bins: int = 10) -> tuple[list[float], list[float]]:
        """Returns (counts, bin_edges)."""
        import numpy as np
        if not self.spreads:
            return [], []
        counts, edges = np.histogram(self.spreads, bins=bins)
        return counts.tolist(), edges.tolist()
