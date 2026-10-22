"""The one order book shared by REST routes and the websocket stream."""
from __future__ import annotations

import threading
from functools import lru_cache

import lobx_cpp

# FastAPI runs sync handlers in a thread pool and the C++ book is not
# thread-safe, so every access goes through this lock.
book_lock = threading.Lock()


@lru_cache(maxsize=1)
def get_order_book() -> lobx_cpp.OrderBook:
    return lobx_cpp.OrderBook()


def snapshot(book: lobx_cpp.OrderBook) -> dict:
    with book_lock:
        return {
            "best_bid": book.best_bid(),
            "best_ask": book.best_ask(),
            "mid_price": book.mid_price(),
            "spread": book.spread(),
            "total_bid_qty": book.total_bid_qty(),
            "total_ask_qty": book.total_ask_qty(),
            "bid_levels": book.bid_levels(),
            "ask_levels": book.ask_levels(),
        }
