"""Spread analytics."""
from __future__ import annotations
from collections import deque
import lobx_cpp


class SpreadMonitor:
    """Tracks time-series of bid-ask spreads."""

    def __init__(self, window: int = 100) -> None:
        self._history: deque[int] = deque(maxlen=window)

    def update(self, book: lobx_cpp.OrderBook) -> None:
        spread = book.spread()
        if spread is not None:
            self._history.append(spread)

    @property
    def current(self) -> int | None:
        return self._history[-1] if self._history else None

    @property
    def mean(self) -> float | None:
        if not self._history:
            return None
        return sum(self._history) / len(self._history)
