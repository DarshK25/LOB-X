"""
Depth Stream Manager — Binance order-book reconstruction.

Implements the official Binance "How to manage a local order book correctly"
algorithm:
  https://binance-docs.github.io/apidocs/spot/en/#how-to-manage-a-local-order-book-correctly

The algorithm in plain English
-------------------------------
Binance's @depth@100ms stream sends *incremental diffs*, not full snapshots.
A diff says "at price P, the total resting quantity is now Q" — if Q == 0, that
price level has been completely removed from the real book.

The problem: when you first connect you have no baseline.  Applying diffs from
that moment would give you a forever-wrong book because you missed everything
that existed before you connected.  The fix is a five-step dance:

  Step 1 → Open the diff-depth WebSocket and BUFFER every event. Don't apply yet.
  Step 2 → Fetch a full REST snapshot (up to 1000 levels on each side).
  Step 3 → Discard any buffered events whose final update ID (u) <= snapshot's
            lastUpdateId — those events are already reflected in the snapshot.
  Step 4 → Find the first remaining buffered event satisfying:
                U <= lastUpdateId + 1 <= u
            This is the sync point.  If no such event exists, go back to step 1.
  Step 5 → Apply that event, then every subsequent event, verifying that each
            new event's first update ID (U) == previous event's final update ID (u) + 1.
            If that ever breaks, the stream has a gap — resync from step 1.

Data structures
---------------
LocalOrderBook uses two sortedcontainers.SortedDict:

    bids: SortedDict[price_float, qty_float]   ascending keys
    asks: SortedDict[price_float, qty_float]   ascending keys

Why SortedDict?
    * Insert / delete: O(log n)  — same as std::map in C++
    * Best bid (max key): bids.peekitem(-1) in O(log n)
    * Best ask (min key): asks.peekitem(0) in O(log n)
    * Any slice of levels (e.g., top-10 depth): asks.islice(0, 10) in O(k)

Why ascending for bids?
    Internally we always want the best bid at the end of the key list so we
    can peekitem(-1) — the last element of an ascending sorted dict is the
    largest key.  This is the standard trick used in production systems to
    avoid maintaining a separate max-heap.

Binance depth event fields
--------------------------
    E  = event time (ms epoch)
    s  = symbol ("BTCUSDT")
    U  = first update ID in this event      ← used for continuity check
    u  = final update ID in this event      ← used for continuity check
    b  = list of [price_str, qty_str] bid changes
    a  = list of [price_str, qty_str] ask changes
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import httpx
from sortedcontainers import SortedDict

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

REST_BASE = "https://api.binance.com"
SNAPSHOT_LIMIT = 1000  # maximum levels Binance returns; always use this


# ── Local Order Book ──────────────────────────────────────────────────────────

class LocalOrderBook:
    """
    A price-level mirror of the exchange order book.

    Stores aggregate resting quantity per price level (not individual orders —
    the public feed never reveals individual order IDs).  Each entry is:

        bids[price] = total_qty_resting_at_this_price   (best bid = highest key)
        asks[price] = total_qty_resting_at_this_price   (best ask = lowest key)

    Contrast with the C++ engine's OrderBook, which stores individual Order
    objects in a FIFO queue per level.  The public feed only gives us totals,
    so this book cannot do FIFO — it is strictly a price→qty map.
    """

    def __init__(self) -> None:
        # SortedDict from sortedcontainers — O(log n) insert/delete, sorted iteration.
        self.bids: SortedDict = SortedDict()  # ascending; best = peekitem(-1)
        self.asks: SortedDict = SortedDict()  # ascending; best = peekitem(0)

    # ── Mutation ──────────────────────────────────────────────────────────────

    def apply_levels(self, side: str, updates: list[tuple[float, float]]) -> None:
        """
        Apply a list of (price, qty) updates to one side.

        Binance semantics: qty == 0 means "remove this price level entirely".
        Otherwise, it is an absolute quantity — not a delta.

        Parameters
        ----------
        side    : "bid" or "ask"
        updates : list of (price_float, qty_float) pairs from the event
        """
        book = self.bids if side == "bid" else self.asks

        for price, qty in updates:
            if qty == 0.0:
                # Price level gone — remove it.  .pop() is a no-op if missing.
                book.pop(price, None)
            else:
                # Absolute qty at this price — overwrite whatever was there.
                book[price] = qty

    # ── Queries ───────────────────────────────────────────────────────────────

    def best_bid(self) -> tuple[float, float] | None:
        """(price, qty) of the best (highest) bid, or None if no bids."""
        return self.bids.peekitem(-1) if self.bids else None

    def best_ask(self) -> tuple[float, float] | None:
        """(price, qty) of the best (lowest) ask, or None if no asks."""
        return self.asks.peekitem(0) if self.asks else None

    def mid_price(self) -> float | None:
        """Arithmetic mid between best bid and best ask, or None."""
        bb = self.best_bid()
        ba = self.best_ask()
        return (bb[0] + ba[0]) / 2.0 if bb and ba else None

    def spread(self) -> float | None:
        """Ask − Bid spread, or None if either side is empty."""
        bb = self.best_bid()
        ba = self.best_ask()
        return ba[0] - bb[0] if bb and ba else None

    def top_of_book(self, n: int = 5) -> dict:
        """
        Return the top-n levels on each side as plain lists.
        Useful for logging, WebSocket broadcasting, and debug.

        Returns
        -------
        {
            "bids": [(price, qty), ...],   # best first (descending price)
            "asks": [(price, qty), ...]    # best first (ascending price)
        }
        """
        bid_keys = self.bids.keys()
        ask_keys = self.asks.keys()
        # Top bids = highest prices → last n keys in ascending SortedDict
        top_bids = [(k, self.bids[k]) for k in reversed(bid_keys[-n:])]
        # Top asks = lowest prices → first n keys in ascending SortedDict
        top_asks = [(k, self.asks[k]) for k in ask_keys[:n]]
        return {"bids": top_bids, "asks": top_asks}

    def __len__(self) -> int:
        """Total number of resting price levels across both sides."""
        return len(self.bids) + len(self.asks)


# ── Depth Stream Manager ──────────────────────────────────────────────────────

@dataclass
class _SyncState:
    """Tracks synchronisation progress so the sync can be restarted cleanly."""
    synced: bool = False
    syncing: bool = False          # True while the REST snapshot fetch is in flight
    last_update_id: int | None = None   # snapshot's lastUpdateId
    prev_final_u: int | None = None     # final update ID of the last applied event
    buffer: list[dict] = field(default_factory=list)


class DepthStreamManager:
    """
    Reconstructs and maintains a live local order book from Binance's
    incremental diff-depth stream.

    Lifecycle
    ---------
    1. Receive WebSocket events via handle_event(event) — called by Collector.
    2. Before sync: buffer every event, kick off _sync() as a background task.
    3. After sync:  apply every event immediately via _apply_event().
    4. If a continuity gap is detected, tear down and resync from scratch.

    Thread/task safety
    ------------------
    All methods are called from a single asyncio event loop (the WS receive
    loop), so no locking is needed.  The only concurrent operation is the REST
    snapshot fetch, which runs in a separate Task but only mutates _state and
    self.book after awaiting its response — by which point the WS loop is in
    the `await asyncio.sleep(0)` poll.
    """

    def __init__(self, symbol: str) -> None:
        self.symbol = symbol.upper()
        self.book = LocalOrderBook()
        self._state = _SyncState()
        self._sync_generation = 0

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def is_synced(self) -> bool:
        """True once the snapshot sync is complete and the book is live."""
        return self._state.synced

    @property
    def sync_generation(self) -> int:
        """Monotonically increasing count of completed REST snapshot syncs."""
        return self._sync_generation

    def mark_desynced(self, reason: str) -> None:
        """Discard book continuity state after a transport disconnect.

        Diff-depth update IDs are only meaningful for one uninterrupted stream.
        Keeping the old cursor across a reconnect can silently apply a new
        stream to a stale book, so every reconnect begins with a new snapshot.
        """
        if self._state.synced or self._state.syncing or self._state.buffer:
            logger.warning("[%s] Marking depth book desynced: %s", self.symbol, reason)
        self.book = LocalOrderBook()
        self._state = _SyncState()

    async def handle_event(self, event: dict) -> None:
        """
        Entry point for every @depth event from the WebSocket.

        Decision tree
        -------------
        A. Not yet synced → buffer this event.
           If no sync is running yet, start one (asyncio.create_task so the
           REST call happens concurrently with more arriving WS events).
        B. Already synced → apply immediately.
        """
        if not self._state.synced:
            self._state.buffer.append(event)
            if not self._state.syncing:
                self._state.syncing = True
                asyncio.create_task(self._sync())  # fire-and-forget background task
        else:
            self._apply_event(event, check_continuity=True)

    # ── Sync algorithm ────────────────────────────────────────────────────────

    async def _sync(self) -> None:
        """
        Fetch a REST snapshot and reconcile with the event buffer.

        This runs as an asyncio Task, concurrently with the WS receive loop.
        While it awaits the REST call, new WS events pile up in self._state.buffer
        — that is exactly what we want (step 1 of the algorithm).
        """
        logger.info("[%s] Fetching REST snapshot (limit=%d)…", self.symbol, SNAPSHOT_LIMIT)

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{REST_BASE}/api/v3/depth",
                    params={"symbol": self.symbol, "limit": SNAPSHOT_LIMIT},
                )
                resp.raise_for_status()
                snapshot: dict = resp.json()
        except Exception as exc:
            logger.error("[%s] REST snapshot failed (%s) — will retry on next event", self.symbol, exc)
            self._state.syncing = False
            return

        last_id: int = snapshot["lastUpdateId"]
        self._state.last_update_id = last_id
        logger.info("[%s] Snapshot received, lastUpdateId=%d", self.symbol, last_id)

        # ── Step 2: apply snapshot to the book ────────────────────────────────
        # Replace whatever partial state we had with the authoritative snapshot.
        self.book = LocalOrderBook()
        self.book.apply_levels(
            "bid", [(float(p), float(q)) for p, q in snapshot["bids"]]
        )
        self.book.apply_levels(
            "ask", [(float(p), float(q)) for p, q in snapshot["asks"]]
        )

        # ── Step 3: discard stale buffered events ──────────────────────────────
        # Events whose final update ID (u) <= lastUpdateId are already reflected
        # in the snapshot — throw them away.
        self._state.buffer = [e for e in self._state.buffer if e["u"] > last_id]

        if not self._state.buffer:
            # No residual events — we're in sync, nothing more to do.
            self._state.synced = True
            self._state.syncing = False
            self._sync_generation += 1
            logger.info("[%s] Synced (no residual events). Book has %d levels.", self.symbol, len(self.book))
            return

        # ── Step 4: find the sync point ───────────────────────────────────────
        # The first remaining event must satisfy:  U <= lastUpdateId + 1 <= u
        # ("this event covers the update immediately after the snapshot").
        first = self._state.buffer[0]
        if not (first["U"] <= last_id + 1 <= first["u"]):
            logger.error(
                "[%s] Snapshot/stream gap (lastUpdateId=%d, buffer[0] U=%d, u=%d) — resyncing",
                self.symbol, last_id, first["U"], first["u"],
            )
            # Trigger a fresh sync on the next incoming event.
            self._state = _SyncState()
            return

        # ── Step 5: apply all residual buffered events ─────────────────────────
        for i, event in enumerate(self._state.buffer):
            self._apply_event(event, check_continuity=(i > 0))
            if not self._state.synced and i == 0:
                # _apply_event set synced=True on the first valid event
                pass  # continue applying the rest

        self._state.buffer.clear()
        self._state.synced = True
        self._state.syncing = False
        self._sync_generation += 1
        logger.info(
            "[%s] Fully synced. Applied %d buffered events. Book: %d levels.",
            self.symbol, i + 1, len(self.book),
        )

    # ── Event application ─────────────────────────────────────────────────────

    def _apply_event(self, event: dict, check_continuity: bool = True) -> None:
        """
        Apply one depth-diff event to the local order book.

        Continuity check
        ----------------
        For every event after the first, Binance guarantees:
            event["U"] == prev_event["u"] + 1

        If this invariant breaks, the stream has a gap — we've missed updates
        and our book is now wrong.  Tear down and resync immediately.

        Event field mapping
        -------------------
        event["b"] → list of bid changes  → apply_levels("bid", ...)
        event["a"] → list of ask changes  → apply_levels("ask", ...)
        event["u"] → final update ID of this event → becomes prev_final_u
        event["U"] → first update ID of this event → checked against prev_final_u + 1
        """
        state = self._state

        if check_continuity and state.prev_final_u is not None:
            expected_U = state.prev_final_u + 1
            actual_U = event["U"]
            if actual_U != expected_U:
                logger.error(
                    "[%s] Update-ID gap! Expected U=%d, got U=%d (prev u=%d). Resyncing.",
                    self.symbol, expected_U, actual_U, state.prev_final_u,
                )
                # Rebuild state and queue a resync.
                self._state = _SyncState(buffer=[event])
                self._state.syncing = True
                asyncio.create_task(self._sync())
                return

        # Apply bid and ask changes from this event.
        self.book.apply_levels(
            "bid", [(float(p), float(q)) for p, q in event.get("b", [])]
        )
        self.book.apply_levels(
            "ask", [(float(p), float(q)) for p, q in event.get("a", [])]
        )

        # Advance the continuity cursor.
        state.prev_final_u = event["u"]
