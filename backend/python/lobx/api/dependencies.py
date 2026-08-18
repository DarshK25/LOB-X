"""FastAPI dependency injection."""
from __future__ import annotations
import lobx_cpp
from functools import lru_cache


@lru_cache(maxsize=1)
def get_order_book() -> lobx_cpp.OrderBook:
    """Singleton OrderBook — shared across all requests."""
    return lobx_cpp.OrderBook()
