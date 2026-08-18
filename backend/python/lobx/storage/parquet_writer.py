"""
Parquet Writer — time-partitioned columnar storage for tick data.

Why Parquet?
------------
Parquet is a columnar binary format built for analytical reads:
    * Column pruning: reading only "price, qty" from a 10-column file is fast
      because unneeded columns are never read off disk.
    * Compression: columnar layout makes run-length and dictionary encodings
      highly effective on repeated values (same symbol, same side, etc.).
    * Zero-cost schema: pyarrow enforces types at write time so downstream
      pandas/numpy code doesn't have to cast.
    * Ecosystem compatibility: pandas, DuckDB, Polars, Spark all read Parquet
      natively — useful when backtesting Phase 2–3 code outside Python.

File rotation strategy (UTC-date-partitioned)
---------------------------------------------
One file per day per stream type:
    data/raw/
        trades_2026-08-02.parquet
        trades_2026-08-03.parquet
        depth_2026-08-02.parquet

At midnight UTC, RotatingParquetWriter closes the current writer and opens a
new file for the new date.  This keeps individual files small (a day of
BTC/USDT tick data is ~200–800 MB) and makes date-range backtests trivial:
    import pyarrow.parquet as pq
    table = pq.read_table("data/raw", filters=[("event_time", ">=", t0)])

In-memory buffer
----------------
Writing to Parquet in row-by-row fashion is slow because pyarrow must
repeatedly build and merge Arrow batches.  We buffer `flush_every` rows in a
plain Python list and flush once as a single pyarrow Table.  At 1000 events/s
(typical for BTC/USDT @depth@100ms), flush_every=5000 means a flush every
~5 seconds — minimal latency impact, much better I/O efficiency than
per-row writes.

Thread safety
-------------
RotatingParquetWriter._lock (threading.Lock) protects the buffer and writer
because the Collector calls write() from the asyncio event loop thread, but
a future enhancement might call close() from a signal handler thread.

Schemas
-------
Defined as module-level constants so they can be imported by Collector without
creating a writer instance — keeps the public API clean.

TRADE_SCHEMA   : one row per @trade event
DEPTH_SCHEMA   : one row per price-level change in a @depth event
                 (a single depth event usually touches 1–20 levels)
SNAPSHOT_SCHEMA: optional — for logging periodic full-book snapshots
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

# ── Schemas ───────────────────────────────────────────────────────────────────

TRADE_SCHEMA = pa.schema([
    # int64 milliseconds since epoch (Binance "E" field)
    pa.field("event_time",      pa.int64()),
    # Binance trade ID — unique, monotonically increasing per symbol
    pa.field("trade_id",        pa.int64()),
    pa.field("price",           pa.float64()),
    pa.field("qty",             pa.float64()),
    # True  = seller was the taker (aggressor); False = buyer was taker
    pa.field("is_buyer_maker",  pa.bool_()),
    pa.field("symbol",          pa.string()),
])

DEPTH_SCHEMA = pa.schema([
    pa.field("event_time",      pa.int64()),   # event["E"]
    pa.field("symbol",          pa.string()),   # event["s"]
    # "bid" or "ask"
    pa.field("side",            pa.string()),
    pa.field("price",           pa.float64()),
    # qty == 0.0 means this level was removed from the book
    pa.field("qty",             pa.float64()),
    # first / final update IDs — needed to reconstruct sync state offline
    pa.field("first_update_id", pa.int64()),   # event["U"]
    pa.field("final_update_id", pa.int64()),   # event["u"]
])

# Optional: periodic full-book snapshots for reconstruction without replaying diffs
SNAPSHOT_SCHEMA = pa.schema([
    pa.field("snapshot_time",   pa.int64()),   # ms epoch when snapshot was taken
    pa.field("symbol",          pa.string()),
    pa.field("side",            pa.string()),
    pa.field("price",           pa.float64()),
    pa.field("qty",             pa.float64()),
    pa.field("last_update_id",  pa.int64()),   # DSM._state.last_update_id at snapshot time
])


# ── Writer ────────────────────────────────────────────────────────────────────

class RotatingParquetWriter:
    """
    Buffers rows and flushes to a UTC-date-partitioned Parquet file.

    Parameters
    ----------
    base_dir    : directory where files are written (created if missing).
    name        : file name prefix, e.g. "trades" → "trades_2026-08-02.parquet".
    schema      : pyarrow.Schema — enforced at write time.
    flush_every : flush the buffer to disk after this many rows accumulate.
                  Tune based on event rate; 5000 is fine for BTC/USDT depth.

    Usage
    -----
        writer = RotatingParquetWriter("data/raw", "trades", TRADE_SCHEMA)
        writer.write({"event_time": 123, "trade_id": 456, ...})
        ...
        writer.close()   # always call on shutdown to flush remaining rows
    """

    def __init__(
        self,
        base_dir: str,
        name: str,
        schema: pa.Schema,
        flush_every: int = 5_000,
    ) -> None:
        self._base_dir = Path(base_dir)
        self._name = name
        self._schema = schema
        self._flush_every = flush_every

        self._buffer: list[dict] = []
        self._current_date: datetime.date | None = None
        self._writer: pq.ParquetWriter | None = None
        self._lock = threading.Lock()

    # ── Public interface ──────────────────────────────────────────────────────

    def write(self, row: dict) -> None:
        """
        Add one row to the buffer, flushing if the threshold is reached.

        row must contain exactly the fields in self._schema — extra fields
        raise a pyarrow ArrowInvalid error at flush time (caught in _flush).
        Missing fields also raise — validate your dict keys match the schema.
        """
        with self._lock:
            self._rotate_if_needed()
            self._buffer.append(row)
            if len(self._buffer) >= self._flush_every:
                self._flush()

    def flush_now(self) -> None:
        """Force an immediate flush regardless of buffer size (e.g., on heartbeat)."""
        with self._lock:
            self._flush()

    def close(self) -> None:
        """
        Flush remaining rows and close the underlying Parquet file.
        ALWAYS call this on process shutdown — unflushed rows are lost otherwise.
        """
        with self._lock:
            self._close_writer()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _rotate_if_needed(self) -> None:
        """
        Check whether UTC date has changed; if so, close the current file and
        open a new one.  Called inside the lock from write().
        """
        today = datetime.now(timezone.utc).date()
        if today == self._current_date:
            return

        # Date changed — close old file, open new one.
        self._close_writer()
        self._current_date = today
        self._base_dir.mkdir(parents=True, exist_ok=True)
        file_path = self._base_dir / f"{self._name}_{today.isoformat()}.parquet"

        # compression="snappy": fast, ~2× compression on numeric tick data.
        # Use "zstd" for smaller files at the cost of slightly slower reads.
        self._writer = pq.ParquetWriter(
            str(file_path), self._schema, compression="snappy"
        )

    def _flush(self) -> None:
        """
        Convert the buffer to a pyarrow Table and write one row group.

        A "row group" in Parquet is a horizontal slice of the table — the
        fundamental unit of compression and column statistics.  Each call to
        write_table() adds one row group to the file.  Fewer, larger row groups
        are better for analytical scans; smaller row groups are better for
        random access.  5000 rows per group is a reasonable default.
        """
        if not self._buffer or self._writer is None:
            return

        try:
            table = pa.Table.from_pylist(self._buffer, schema=self._schema)
            self._writer.write_table(table)
        except pa.ArrowInvalid as exc:
            # Schema mismatch — log the offending row for debugging.
            import logging
            logging.getLogger(__name__).error(
                "Schema mismatch flushing %s: %s. First offending row: %s",
                self._name, exc, self._buffer[0] if self._buffer else "empty",
            )
        finally:
            # Always clear buffer so we don't re-write bad rows on retry.
            self._buffer.clear()

    def _close_writer(self) -> None:
        """Flush remaining rows then close the file handle."""
        self._flush()
        if self._writer is not None:
            self._writer.close()
            self._writer = None
