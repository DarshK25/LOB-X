"""
Binance WebSocket Client — raw connection layer.

Owns one combined-stream WebSocket connection, reconnects automatically with
exponential back-off, and hands every parsed message to an async callback.

Streams use Binance's market-data-only mirror endpoint:
    wss://data-stream.binance.vision/stream?streams=<name1>/<name2>/...

This endpoint requires NO API key and is specifically intended for public
market data consumers.  Each message from a combined stream looks like:
    {"stream": "btcusdt@depth@100ms", "data": { ... }}

Key design decisions
--------------------
* `websockets` auto-answers server pings (RFC 6455 §5.5.2) — we don't need
  any manual pong logic.
* Binance closes connections after 24 h; we treat any close/OSError as a
  normal reconnect trigger, not a fatal error.
* Back-off caps at 30 s so a multi-hour outage doesn't freeze the process.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable

import websockets
import websockets.exceptions

logger = logging.getLogger(__name__)

# ── Endpoints ─────────────────────────────────────────────────────────────────

# Market-data-only mirror — best choice for pure data collection projects.
# No auth required; same data as the production endpoint.
BINANCE_WS_DATA_ONLY = "wss://data-stream.binance.vision/stream"

# Production endpoint — identical data, slightly higher stability SLA.
BINANCE_WS_PROD = "wss://stream.binance.com:9443/stream"

# Spot testnet — use during development to avoid hammering prod.
BINANCE_WS_TESTNET = "wss://testnet.binance.vision/ws"

# ── Type alias ────────────────────────────────────────────────────────────────
MessageCallback = Callable[[dict], Awaitable[None]]
DisconnectCallback = Callable[[Exception], Awaitable[None]]


class BinanceWSClient:
    """
    Manages one combined WebSocket stream connection to Binance.

    Parameters
    ----------
    streams   : list of stream names, e.g. ["btcusdt@depth@100ms", "btcusdt@trade"]
    on_message: async callable invoked with the parsed dict of every message.
    base_url  : which Binance endpoint to connect to (default: data-only mirror).

    Usage
    -----
        client = BinanceWSClient(["btcusdt@depth@100ms", "btcusdt@trade"], handler)
        await client.run()   # blocks; call client.stop() from another task to exit
    """

    def __init__(
        self,
        streams: list[str],
        on_message: MessageCallback,
        base_url: str = BINANCE_WS_DATA_ONLY,
        on_disconnect: DisconnectCallback | None = None,
    ) -> None:
        # Build the combined-stream URL.  "/" separates stream names.
        self._url = f"{base_url}?streams={'/'.join(streams)}"
        self._on_message = on_message
        self._on_disconnect = on_disconnect
        self._stop = False
        self._current_ws = None

    # ── Public interface ──────────────────────────────────────────────────────

    async def run(self) -> None:
        """
        Connect, receive messages, reconnect on failure.

        Reconnect algorithm
        -------------------
        1.  Connect.  On success reset back-off to 1 s.
        2.  Iterate messages in a tight async loop — no sleep, no polling.
        3.  On disconnect (ConnectionClosed or OSError) wait `backoff` seconds,
            then double `backoff` (capped at MAX_BACKOFF_S).
        4.  Repeat until self._stop is True.
        """
        backoff_s = 1
        MAX_BACKOFF_S = 30

        while not self._stop:
            try:
                async with websockets.connect(
                    self._url,
                    ping_interval=180,   # send WS ping every 3 min — keeps the conn alive
                    ping_timeout=600,    # wait up to 10 min for pong before declaring dead
                    open_timeout=15,     # fail fast if server doesn't accept within 15 s
                ) as ws:
                    self._current_ws = ws
                    logger.info("Connected to %s", self._url)
                    backoff_s = 1  # clean connect → reset back-off

                    async for raw_message in ws:
                        # `async for` yields one str/bytes per WebSocket frame.
                        # Binance combined-stream frames are always UTF-8 JSON.
                        parsed: dict = json.loads(raw_message)
                        await self._on_message(parsed)

                    # ``WebSocketClientProtocol.close()`` can end an async
                    # iterator cleanly instead of raising ConnectionClosed.
                    # It is still a transport boundary, so the depth cursor
                    # must be invalidated before opening a new connection.
                    if not self._stop:
                        raise OSError("WebSocket stream ended")

            except (websockets.exceptions.ConnectionClosed, OSError) as exc:
                if self._on_disconnect is not None:
                    await self._on_disconnect(exc)
                logger.warning(
                    "Disconnected (%s). Retrying in %d s …", exc, backoff_s
                )
                await asyncio.sleep(backoff_s)
                backoff_s = min(backoff_s * 2, MAX_BACKOFF_S)
            finally:
                self._current_ws = None

    def stop(self) -> None:
        """Signal the run() loop to exit after the next reconnect attempt."""
        self._stop = True

    async def force_disconnect(self) -> None:
        """Close the live socket so the normal reconnect path is exercised.

        This is intentionally an awaitable test hook: setting a flag and waiting
        for another market-data frame does not prove that the transport dropped.
        """
        if self._current_ws is None:
            raise RuntimeError("Cannot force a disconnect before the socket connects")
        await self._current_ws.close(code=1001, reason="simulated disconnect for test")
