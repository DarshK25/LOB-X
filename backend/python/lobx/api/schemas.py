"""
Pydantic schemas for the REST API and WebSocket messages.
"""
from __future__ import annotations
from pydantic import BaseModel, Field


class OrderRequest(BaseModel):
    order_id: int = Field(..., description="Unique order ID")
    price:    int = Field(..., gt=0, description="Limit price in ticks")
    qty:      int = Field(..., gt=0, description="Order quantity in lots")
    is_buy:   bool
    tif:      str = Field("GTC", pattern="^(GTC|IOC|FOK)$")


class TradeResponse(BaseModel):
    buy_id:  int
    sell_id: int
    price:   int
    qty:     int


class BookSnapshot(BaseModel):
    best_bid:      int | None
    best_ask:      int | None
    mid_price:     float | None
    spread:        int | None
    total_bid_qty: int
    total_ask_qty: int
    bid_levels:    int
    ask_levels:    int


class CancelRequest(BaseModel):
    order_id: int


class SimulationConfig(BaseModel):
    ticks:     int   = Field(1000, gt=0)
    mid_price: int   = Field(10_000, gt=0)
    tick_ms:   int   = Field(100, gt=0)
    seed:      int | None = None
