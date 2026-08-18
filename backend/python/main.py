"""
LOB-X FastAPI entrypoint.

Run with:
    uvicorn main:app --reload --host 0.0.0.0 --port 8000
"""
from __future__ import annotations
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware

import lobx_cpp
from lobx.api.routes import router
from lobx.api.websocket import book_stream

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("lobx")

# ── Shared state ──────────────────────────────────────────────────────────────
book = lobx_cpp.OrderBook()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    log.info("LOB-X starting — C++ engine loaded ✓")
    yield
    log.info("LOB-X shutting down")


app = FastAPI(
    title       = "LOB-X",
    description = "High-performance limit order book exchange API",
    version     = "0.1.0",
    lifespan    = lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins     = ["*"],
    allow_credentials = True,
    allow_methods     = ["*"],
    allow_headers     = ["*"],
)

app.include_router(router, prefix="/api/v1")


@app.websocket("/ws/book")
async def websocket_book(websocket: WebSocket) -> None:
    await book_stream(websocket, book)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
