"""WebSocket stream of book snapshots from the shared book."""
from __future__ import annotations

import asyncio

from fastapi import WebSocket, WebSocketDisconnect

from lobx.api.dependencies import get_order_book, snapshot


async def book_stream(websocket: WebSocket, hz: float = 10.0) -> None:
    await websocket.accept()
    book = get_order_book()
    try:
        while True:
            await websocket.send_json({"type": "book_snapshot", **snapshot(book)})
            await asyncio.sleep(1.0 / hz)
    except WebSocketDisconnect:
        pass
