"""REST routes for the matching engine. All routes use the shared book."""
from __future__ import annotations

import lobx_cpp
from fastapi import APIRouter, Depends, HTTPException

from lobx.api.dependencies import book_lock, get_order_book, snapshot
from lobx.api.schemas import BookSnapshot, OrderRequest, TradeResponse

router = APIRouter()

_TIF = {"GTC": lobx_cpp.TimeInForce.GTC, "IOC": lobx_cpp.TimeInForce.IOC,
        "FOK": lobx_cpp.TimeInForce.FOK}


@router.get("/book", response_model=BookSnapshot)
def get_book(book: lobx_cpp.OrderBook = Depends(get_order_book)) -> BookSnapshot:
    return BookSnapshot(**snapshot(book))


@router.post("/order", response_model=list[TradeResponse])
def submit_order(req: OrderRequest,
                 book: lobx_cpp.OrderBook = Depends(get_order_book)) -> list[TradeResponse]:
    order = lobx_cpp.Order(req.order_id, req.price, req.qty, req.is_buy, _TIF[req.tif])
    with book_lock:
        trades = book.add_limit_order(order)
    return [TradeResponse(buy_id=t.buy_id, sell_id=t.sell_id, price=t.price, qty=t.qty)
            for t in trades]


@router.delete("/order/{order_id}")
def cancel_order(order_id: int, book: lobx_cpp.OrderBook = Depends(get_order_book)) -> dict:
    with book_lock:
        ok = book.cancel(order_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Order {order_id} not found")
    return {"cancelled": order_id}
