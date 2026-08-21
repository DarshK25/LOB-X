"""
Collector — the central orchestrator of the Phase 1 data pipeline.

╔══════════════════════════════════════════════════════════════════════╗
║  DATA FLOW (read this first — it answers the "how does it connect"  ║
║  question completely)                                                ║
╚══════════════════════════════════════════════════════════════════════╝

  Binance Exchange (real live market)
         │
         │ WebSocket frames arrive ~10× / second
         │ Two streams in one connection:
         │   btcusdt@depth@100ms  →  incremental depth diff events
         │   btcusdt@trade        →  every individual match
         ▼
  BinanceWSClient
    receive loop (async for frame in websocket)
    parse JSON → call _on_message(msg)
         │
         │ msg = {"stream": "btcusdt@depth@100ms", "data": {...}}
         │
         ├─── "@depth" in stream ──────────────────────────────────────────►
         │                                                                  │
         │    DepthStreamManager.handle_event(event)                        │
         │      Phase A (before sync):                                      │
         │        buffer this event; kick off REST snapshot fetch           │
         │      Phase B (after sync):                                       │
         │        apply diff to LocalOrderBook (SortedDict bids/asks)       │
         │        check U == prev_u + 1; resync if broken                   │
         │                │                                                 │
         │                ▼                                                 │
         │    ORDER BOOK #1 — LocalOrderBook (Python side)                  │
         │    ┌────────────────────────────────────────────┐               │
         │    │  bids: SortedDict{price → total_qty}       │               │
         │    │  asks: SortedDict{price → total_qty}       │               │
         │    │  best_bid(), best_ask(), mid_price()       │               │
         │    └────────────────────────────────────────────┘               │
         │      ↑ This is the REFERENCE book. You can                      │
         │        compare it against Binance's live UI                     │
         │        to prove the sync algorithm is correct.                  │
         │                │                                                 │
         │                ├──► RotatingParquetWriter (depth rows)           │
         │                │      one row per price level that changed       │
         │                │      flushed every 500 rows OR 30-sec heartbeat │
         │                │                                                 │
         │                └──► EngineMirrorBridge (if C++ built)            │
         │                       convert float→ticks, qty→lots              │
         │                       cancel old synthetic order                  │
         │                       insert new synthetic order                  │
         │                                │                                 │
         │                                ▼                                 │
         │              ORDER BOOK #2 — lobx_cpp.OrderBook (C++ side)       │
         │              ┌─────────────────────────────────────────────────┐ │
         │              │  std::map<Price, PriceLevel> bids (descending)  │ │
         │              │  std::map<Price, PriceLevel> asks (ascending)   │ │
         │              │  best_bid(), best_ask(), mid_price()             │ │
         │              └─────────────────────────────────────────────────┘ │
         │                ↑ This is the ENGINE book. Phase 2 strategies     │
         │                  (AvellanedaStoikov, AlmgrenChriss) will quote   │
         │                  against THIS book, not the Python one.          │
         │                                                                  │
         │                ↓ on_book_update callbacks fire here ─────────────┘
         │                  (watch_live.py, verify_engine_mirror.py,
         │                   later: StrategyRunner)
         │
         └─── "@trade" in stream ─────────────────────────────────────────►
                                                                            │
              parse_trade_event(event) → TradeTick                          │
                │                                                           │
                ├──► RotatingParquetWriter (trade rows)                     │
                │      one row per trade (price, qty, taker side, time)     │
                │                                                           │
                └──► on_trade callbacks fire here ─────────────────────────┘
                       (watch_live.py prints these in real time;
                        Phase 3 Hawkes calibration reads the Parquet file)

WHY TWO ORDER BOOKS?
────────────────────
LocalOrderBook is pure Python, easy to inspect from a REPL, and lets you
compare best_bid/best_ask against Binance's own UI — that's your correctness
proof for the sync algorithm.

lobx_cpp.OrderBook is your actual matching engine — the thing your Phase 2
strategy calls add_limit_order() and cancel() on to place and cancel quotes.
It runs at C++ speed because strategies call it thousands of times per second.

The EngineMirrorBridge keeps both in sync: every depth event goes to both.
"""
from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Callable
from typing import TYPE_CHECKING

from lobx.market_data.binance_ws_client import BinanceWSClient
from lobx.market_data.depth_stream_manager import DepthStreamManager
from lobx.market_data.trade_stream import TradeTick, parse_trade_event
from lobx.storage.parquet_writer import (
    DEPTH_SCHEMA,
    TRADE_SCHEMA,
    RotatingParquetWriter,
)
from lobx.storage.postgres_writer import PostgresTradeWriter

if TYPE_CHECKING:
    from lobx.market_data.engine_mirror import EngineMirrorBridge

logger = logging.getLogger(__name__)

# ── Callback type aliases ─────────────────────────────────────────────────────
#
# on_book_update callbacks receive the raw depth event dict (same format as
# Binance sends) AFTER it has already been applied to both LocalOrderBook and
# the C++ engine.  Read collector.depth_mgr.book inside the callback to see
# the updated book state.
#
# on_trade callbacks receive a fully-parsed TradeTick object.
BookUpdateCallback = Callable[[dict], None]
TradeCallback = Callable[[TradeTick], None]


class Collector:
    """
    Orchestrates the full Phase 1 data pipeline.

    Parameters
    ----------
    symbol              : Binance trading pair, e.g. "BTCUSDT".
    data_dir            : directory where Parquet files are written.
    enable_engine_mirror: feed data into the C++ engine too.
                          Set False if lobx_cpp is not compiled yet.
    flush_every         : buffer this many Parquet rows before flushing.
                          500 = flush every ~10 s at typical BTC depth rates.
    heartbeat_s         : force-flush interval in seconds regardless of row count.
                          Also logs live bid/ask/spread to INFO.

    Attributes (read these from outside)
    ────────────────────────────────────
    depth_mgr      : DepthStreamManager
                     .book → LocalOrderBook (SortedDict, Python side)
                     .is_synced → bool
    engine_mirror  : EngineMirrorBridge | None
                     .book → lobx_cpp.OrderBook (C++ side)
                     None if enable_engine_mirror=False or C++ not built.
    """

    def __init__(
        self,
        symbol: str = "BTCUSDT",
        data_dir: str = "data/raw",
        enable_engine_mirror: bool = True,
        flush_every: int = 500,
        heartbeat_s: int = 30,
    ) -> None:
        self.symbol = symbol.upper()
        self._heartbeat_s = heartbeat_s

        # ── Order Book #1: Python reference book ──────────────────────────────
        self.depth_mgr = DepthStreamManager(self.symbol)
        self._written_snapshot_generation = 0

        # ── Order Book #2: C++ engine book (optional) ─────────────────────────
        self.engine_mirror: EngineMirrorBridge | None = None
        if enable_engine_mirror:
            try:
                from lobx.market_data.engine_mirror import EngineMirrorBridge  # noqa: PLC0415
                self.engine_mirror = EngineMirrorBridge()
                logger.info("C++ engine mirror active for %s.", self.symbol)
            except ImportError:
                logger.warning(
                    "lobx_cpp not importable — engine mirror disabled. "
                    "Build the C++ extension to enable it (see docs/architecture.md)."
                )

        # ── Storage — Parquet (local / artifact) ──────────────────────────────
        # flush_every=500: at BTC/USDT rates (~50 depth rows/s after sync),
        # this flushes every ~10 s.  Files stay readable from another terminal.
        self._trade_writer = RotatingParquetWriter(
            data_dir, "trades", TRADE_SCHEMA, flush_every=flush_every
        )
        self._depth_writer = RotatingParquetWriter(
            data_dir, "depth", DEPTH_SCHEMA, flush_every=flush_every
        )

        # ── Storage — PostgreSQL (Neon, persistent long-term) ─────────────────
        # Trades are also streamed to Neon for persistent SQL-queryable storage.
        # DATABASE_URL env var must be set (added to GitHub Actions as a secret).
        # If not set, PostgresTradeWriter is disabled silently — local dev still works.
        self._pg_writer = PostgresTradeWriter(database_url=os.environ.get("DATABASE_URL"))

        # ── Callbacks (register with on_book_update / on_trade) ───────────────
        # These fire AFTER storage writes, so callbacks always see consistent state.
        self._book_cbs: list[BookUpdateCallback] = []
        self._trade_cbs: list[TradeCallback] = []

        # ── WebSocket client ──────────────────────────────────────────────────
        sym = self.symbol.lower()
        self._ws = BinanceWSClient(
            streams=[f"{sym}@depth@100ms", f"{sym}@trade"],
            on_message=self._on_message,
            on_disconnect=self._on_disconnect,
        )

        self.ws_status = "Disconnected"
        self.ws_error: str | None = None


    # ── Callback registration (call BEFORE run()) ─────────────────────────────

    def on_book_update(self, callback: BookUpdateCallback) -> None:
        """
        Register a callback invoked after every synced depth event.

        The callback fires AFTER:
          - depth_mgr.book has been updated (LocalOrderBook)
          - Parquet writer has received the row
          - engine_mirror has been updated (if active)

        So inside your callback you can safely read:
          collector.depth_mgr.book.best_bid()   ← Python book, already updated
          collector.engine_mirror.book.best_bid() ← C++ book, already updated

        Parameters
        ----------
        callback : callable(event: dict) → None
            event is the raw Binance depth event dict, same fields as the WS message.
        """
        self._book_cbs.append(callback)

    def on_trade(self, callback: TradeCallback) -> None:
        """
        Register a callback invoked after every trade event is parsed.

        Parameters
        ----------
        callback : callable(tick: TradeTick) → None
        """
        self._trade_cbs.append(callback)

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def run(self) -> None:
        """
        Start the pipeline.  Blocks until stop() is called or KeyboardInterrupt.

        Runs concurrent asyncio tasks:
          1. ws-receive        : WebSocket receive loop (reconnects automatically)
          2. parquet-heartbeat : force-flush Parquet + log live book state every N s
        """
        logger.info("Collector starting for %s", self.symbol)

        # Connect to Neon PostgreSQL in the background (no-op if DATABASE_URL is not set)
        db_task = asyncio.create_task(self._pg_writer.connect())

        self.ws_status = "Connecting..."
        ws_task = asyncio.create_task(self._ws.run())
        hb_task = asyncio.create_task(self._heartbeat())
        try:
            await asyncio.gather(ws_task, hb_task, db_task)
        except asyncio.CancelledError:
            ws_task.cancel()
            hb_task.cancel()
            db_task.cancel()
            raise

    def stop(self) -> None:
        """Signal the WebSocket to stop reconnecting."""
        self._ws.stop()

    async def force_disconnect(self) -> None:
        """Testing hook which drops the live transport through the full pipeline."""
        await self._ws.force_disconnect()

    def close(self) -> None:
        """
        Flush all buffers and close Parquet file handles.
        ALWAYS call this on shutdown (the run_collector.py entrypoint does it
        in a try/finally block — make sure your own scripts do the same).

        Note: Postgres writer is closed via close_async() — the sync close()
        here handles only Parquet. The run_collector.py entrypoint calls
        close_async() if an event loop is available.
        """
        logger.info("Flushing and closing Parquet writers…")
        self._trade_writer.close()
        self._depth_writer.close()
        logger.info("Parquet writers closed.")

    async def close_async(self) -> None:
        """Flush Postgres buffer and close pool. Call this on graceful shutdown."""
        self.close()
        await self._pg_writer.close()
        logger.info("Collector fully closed (Parquet + PostgreSQL).")


    # ── Message routing ───────────────────────────────────────────────────────

    async def _on_message(self, msg: dict) -> None:
        """
        Called by BinanceWSClient for every WebSocket message.

        Combined-stream envelope format:
            { "stream": "btcusdt@depth@100ms", "data": { ...event fields... } }

        We strip the envelope and route by stream name.
        """
        self.ws_status = "Connected"
        self.ws_error = None
        stream: str = msg.get("stream", "")
        event: dict = msg.get("data", msg)  # fallback: some modes omit the envelope

        if "@depth" in stream:
            await self._handle_depth(event)
        elif "@trade" in stream:
            self._handle_trade(event)

    async def _on_disconnect(self, exc: Exception) -> None:
        """Invalidate depth state before BinanceWSClient reconnects."""
        self.ws_status = "Disconnected"
        self.ws_error = str(exc)
        self.depth_mgr.mark_desynced(f"WebSocket disconnected: {exc}")

    # ── Depth pipeline ────────────────────────────────────────────────────────

    async def _handle_depth(self, event: dict) -> None:
        """
        Process one @depth@100ms event end-to-end.

        Step-by-step:
        1.  Pass to DepthStreamManager.
              ↳ Pre-sync: event is buffered; REST snapshot kicked off once.
              ↳ Post-sync: diff is applied to LocalOrderBook immediately.
        2.  If not yet synced → return early. Don't write garbage to Parquet.
        3.  Write one row per price-level change to the depth Parquet file.
              Each row captures: event_time, symbol, side, price, qty,
              first_update_id, final_update_id.
              qty == 0.0 rows are kept — they record level removals, which
              a backtester needs to correctly reconstruct historical books.
        4.  Apply to C++ engine mirror (if active).
        5.  Fire all registered on_book_update callbacks.
        """
        await self.depth_mgr.handle_event(event)

        if not self.depth_mgr.is_synced:
            return  # pre-sync: don't write partial / unverified state

        # Persist the authoritative REST-derived state on every transition to
        # synced.  Diff updates alone cannot reconstruct the initial book in a
        # later offline analysis, which made quote-distance calibration depend
        # on an unknown starting state.
        if self.depth_mgr.sync_generation > self._written_snapshot_generation:
            self._write_depth_snapshot(event)
            self._written_snapshot_generation = self.depth_mgr.sync_generation

        # ── Step 3: write to Parquet ──────────────────────────────────────────
        event_time = int(event["E"])
        symbol     = str(event["s"])
        first_uid  = int(event["U"])
        final_uid  = int(event["u"])

        for price_s, qty_s in event.get("b", []):
            self._depth_writer.write({
                "event_time":      event_time,
                "symbol":          symbol,
                "side":            "bid",
                "price":           float(price_s),
                "qty":             float(qty_s),
                "first_update_id": first_uid,
                "final_update_id": final_uid,
            })
        for price_s, qty_s in event.get("a", []):
            self._depth_writer.write({
                "event_time":      event_time,
                "symbol":          symbol,
                "side":            "ask",
                "price":           float(price_s),
                "qty":             float(qty_s),
                "first_update_id": first_uid,
                "final_update_id": final_uid,
            })

        # ── Step 4: update C++ engine ─────────────────────────────────────────
        if self.engine_mirror is not None:
            self.engine_mirror.apply_depth_event(event)

        # ── Step 5: fire callbacks ────────────────────────────────────────────
        for cb in self._book_cbs:
            try:
                cb(event)
            except Exception:
                logger.exception("on_book_update callback raised")

    def _write_depth_snapshot(self, event: dict) -> None:
        """Write the complete, already-synced local book as a Parquet baseline."""
        event_time = int(event["E"])
        first_uid = int(event["U"])
        final_uid = int(event["u"])
        for side, levels in (("bid", self.depth_mgr.book.bids), ("ask", self.depth_mgr.book.asks)):
            for price, qty in levels.items():
                self._depth_writer.write({
                    "event_time": event_time,
                    "symbol": self.symbol,
                    "side": side,
                    "price": float(price),
                    "qty": float(qty),
                    "first_update_id": first_uid,
                    "final_update_id": final_uid,
                })
        logger.info("[%s] Wrote %d-level depth snapshot baseline.", self.symbol, len(self.depth_mgr.book))

    # ── Trade pipeline ────────────────────────────────────────────────────────

    def _handle_trade(self, event: dict) -> None:
        """
        Process one @trade event end-to-end.

        Trades are self-contained — no snapshot dance needed.
        1.  Parse into TradeTick.
        2.  Write to trades Parquet file.
        3.  Fire on_trade callbacks.
        """
        tick = parse_trade_event(event)
        self._trade_writer.write(tick.to_dict())
        self._pg_writer.write(tick)
        for cb in self._trade_cbs:
            try:
                cb(tick)
            except Exception:
                logger.exception("on_trade callback raised")

    # ── Heartbeat ─────────────────────────────────────────────────────────────

    async def _heartbeat(self) -> None:
        """
        Every heartbeat_s seconds:
          1. Force-flush both Parquet writers (so files are always readable).
          2. Log live book state (bid/ask/spread/levels) at INFO level.
        """
        while True:
            await asyncio.sleep(self._heartbeat_s)
            self._trade_writer.flush_now()
            self._depth_writer.flush_now()

            book = self.depth_mgr.book
            bb = book.best_bid()
            ba = book.best_ask()
            if bb and ba:
                logger.info(
                    "[%s] heartbeat | bid=%.2f×%.4f  ask=%.2f×%.4f  "
                    "spread=%.2f  levels=%d bid/%d ask  synced=%s",
                    self.symbol,
                    bb[0], bb[1], ba[0], ba[1],
                    ba[0] - bb[0],
                    len(book.bids), len(book.asks),
                    self.depth_mgr.is_synced,
                )
            else:
                logger.info("[%s] heartbeat | book not ready yet (synced=%s)",
                            self.symbol, self.depth_mgr.is_synced)
