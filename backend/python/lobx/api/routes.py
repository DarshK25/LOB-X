"""
FastAPI route handlers.
"""
from __future__ import annotations
from fastapi import APIRouter, HTTPException
import lobx_cpp

from lobx.api.schemas import (
    OrderRequest, TradeResponse, BookSnapshot,
    CancelRequest, SimulationConfig,
)

router = APIRouter()

# ── Shared state (injected via dependency in production) ───────────────────────
# For dev purposes the book lives here; use dependencies.py for proper DI.
_book = lobx_cpp.OrderBook()


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


@router.get("/book", response_model=BookSnapshot)
def get_book() -> BookSnapshot:
    return BookSnapshot(
        best_bid      = _book.best_bid(),
        best_ask      = _book.best_ask(),
        mid_price     = _book.mid_price(),
        spread        = _book.spread(),
        total_bid_qty = _book.total_bid_qty(),
        total_ask_qty = _book.total_ask_qty(),
        bid_levels    = _book.bid_levels(),
        ask_levels    = _book.ask_levels(),
    )


@router.post("/order", response_model=list[TradeResponse])
def submit_order(req: OrderRequest) -> list[TradeResponse]:
    tif_map = {"GTC": lobx_cpp.TimeInForce.GTC,
               "IOC": lobx_cpp.TimeInForce.IOC,
               "FOK": lobx_cpp.TimeInForce.FOK}
    order  = lobx_cpp.Order(req.order_id, req.price, req.qty, req.is_buy, tif_map[req.tif])
    trades = _book.add_limit_order(order)
    return [TradeResponse(buy_id=t.buy_id, sell_id=t.sell_id,
                          price=t.price, qty=t.qty) for t in trades]


@router.delete("/order/{order_id}")
def cancel_order(order_id: int) -> dict:
    ok = _book.cancel(order_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Order {order_id} not found")
    return {"cancelled": order_id}
