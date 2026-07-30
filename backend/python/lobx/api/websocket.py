"""
WebSocket endpoint — streams live book snapshots and trade events.
"""
from __future__ import annotations
import asyncio
import json
from fastapi import WebSocket, WebSocketDisconnect
import lobx_cpp


class ConnectionManager:
    def __init__(self) -> None:
        self._active: list[WebSocket] = []

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._active.append(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self._active.remove(ws)

    async def broadcast(self, message: dict) -> None:
        payload = json.dumps(message)
        dead    = []
        for ws in self._active:
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._active.remove(ws)


manager = ConnectionManager()


async def book_stream(websocket: WebSocket, book: lobx_cpp.OrderBook) -> None:
    """Stream book snapshots at 10 Hz."""
    await manager.connect(websocket)
    try:
        while True:
            snapshot = {
                "type":          "book_snapshot",
                "best_bid":      book.best_bid(),
                "best_ask":      book.best_ask(),
                "mid_price":     book.mid_price(),
                "spread":        book.spread(),
                "total_bid_qty": book.total_bid_qty(),
                "total_ask_qty": book.total_ask_qty(),
            }
            await websocket.send_text(json.dumps(snapshot))
            await asyncio.sleep(0.1)
    except WebSocketDisconnect:
        manager.disconnect(websocket)
