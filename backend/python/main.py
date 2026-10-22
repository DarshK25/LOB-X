"""LOB-X matching-engine API.

    uvicorn main:app --reload --host 0.0.0.0 --port 8000

REST and the websocket share one C++ order book (``lobx.api.dependencies``).
"""
from __future__ import annotations

import logging

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware

from lobx.api.routes import router
from lobx.api.websocket import book_stream

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="LOB-X", description="Price-time priority matching engine (C++ core)",
              version="0.2.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(router, prefix="/api/v1")


@app.websocket("/ws/book")
async def websocket_book(websocket: WebSocket) -> None:
    await book_stream(websocket)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
