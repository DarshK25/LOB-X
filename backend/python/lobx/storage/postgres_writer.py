"""
PostgreSQL Trade Writer — persistent long-term storage for trade ticks.

Why PostgreSQL (Neon) for trades but Parquet for depth?
------------------------------------------------------
Neon's free tier has a 0.5 GB storage limit.
At a typical BTC/USDT trade rate (~3 trades/sec), a 3-hour collection run generates
~32,400 trades, taking ~3.2 MB of storage. A full day of trades is ~25 MB, which fits
comfortably on the free tier for weeks/months and allows fast SQL queries for Hawkes
process calibration.

However, order book depth changes at ~50 events/sec. Writing all depth changes to
Postgres would consume ~108 MB per 3-hour run (~864 MB/day), exceeding the free tier
limit immediately. Therefore, depth data is kept exclusively in Parquet format via
RotatingParquetWriter and uploaded as GitHub Artifacts (30-day retention).

This class uses `asyncpg` for high-performance async database operations.
If DATABASE_URL is not set or asyncpg cannot be imported, the writer disables
itself and runs in no-op mode so local development/testing without Postgres
still works seamlessly.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from lobx.market_data.trade_stream import TradeTick

logger = logging.getLogger(__name__)

# Handle optional asyncpg dependency gracefully
try:
    import asyncpg
    HAS_ASYNCPG = True
except ImportError:
    HAS_ASYNCPG = False
    logger.warning("asyncpg not installed. PostgreSQL storage will be disabled.")


class PostgresTradeWriter:
    """
    Asynchronously batches and writes trade ticks to Neon PostgreSQL.
    """

    def __init__(self, database_url: str | None = None) -> None:
        self._url = database_url or os.environ.get("DATABASE_URL")
        self._pool: asyncpg.Pool | None = None
        self._buffer: list[tuple[int, int, float, float, float, bool, str, str]] = []
        self._flush_every = 200
        
        # Enable only if asyncpg is present and a database URL is configured
        self._enabled = HAS_ASYNCPG and bool(self._url)
        self.last_error: str | None = None
        
        self._lock = asyncio.Lock()

    @property
    def is_enabled(self) -> bool:
        """Return True if PostgreSQL writes are active."""
        return self._enabled

    async def connect(self) -> None:
        """Establish database connection pool and run migration/schema checks."""
        if not self._enabled:
            return

        try:
            # Serverless databases close idle connections quickly.
            # Keep max size small (5) and set appropriate timeouts.
            self._pool = await asyncpg.create_pool(
                self._url,
                min_size=1,
                max_size=5,
                command_timeout=30.0,
            )
            await self._ensure_schema()
            self.last_error = None
        except Exception as e:
            self.last_error = str(e)
            logger.warning(
                "Failed to connect to PostgreSQL database: %s. "
                "PostgreSQL trade logging disabled for this session.",
                e
            )
            self._enabled = False
            self._pool = None

    async def _ensure_schema(self) -> None:
        """Run DDL queries to create the trades table and indexes if they do not exist."""
        if self._pool is None:
            return

        ddl = """
        CREATE TABLE IF NOT EXISTS trades (
            event_time     BIGINT           NOT NULL,
            trade_id       BIGINT           NOT NULL,
            price          DOUBLE PRECISION NOT NULL,
            qty            DOUBLE PRECISION NOT NULL,
            notional       DOUBLE PRECISION NOT NULL,
            is_buyer_maker BOOLEAN          NOT NULL,
            taker_side     TEXT             NOT NULL,
            symbol         TEXT             NOT NULL,
            inserted_at    TIMESTAMPTZ      NOT NULL DEFAULT now(),
            PRIMARY KEY (symbol, trade_id)
        );
        CREATE INDEX IF NOT EXISTS trades_event_time_idx ON trades (symbol, event_time DESC);
        """
        async with self._pool.acquire() as conn:
            # Execute as a single transaction block
            async with conn.transaction():
                await conn.execute(ddl)
            logger.info("PostgreSQL trades schema verified.")

    def write(self, tick: TradeTick) -> None:
        """
        Buffer a trade tick for insertion. 
        Synchronous wrapper method called directly from the collector's trade loop.
        Triggers an async flush task if the buffer threshold is exceeded.
        """
        if not self._enabled:
            return

        row = (
            tick.event_time,
            tick.trade_id,
            tick.price,
            tick.qty,
            tick.notional,
            tick.is_buyer_maker,
            tick.taker_side,
            tick.symbol,
        )
        self._buffer.append(row)
        
        if len(self._buffer) >= self._flush_every:
            # Fire-and-forget the async flush so we do not block the WebSocket recv loop
            asyncio.create_task(self.flush())

    async def flush(self) -> None:
        """Flush all buffered trade ticks to PostgreSQL using executemany/ON CONFLICT DO NOTHING."""
        if not self._enabled or not self._buffer or self._pool is None:
            return

        async with self._lock:
            # Snapshot the buffer and clear it to accept new incoming trades during I/O
            to_flush = list(self._buffer)
            self._buffer.clear()

            if not to_flush:
                return

            try:
                insert_sql = """
                INSERT INTO trades (
                    event_time, trade_id, price, qty, notional, is_buyer_maker, taker_side, symbol
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                ON CONFLICT (symbol, trade_id) DO NOTHING
                """
                async with self._pool.acquire() as conn:
                    # Executemany is highly optimized in asyncpg for batch queries
                    await conn.executemany(insert_sql, to_flush)
                logger.debug("Successfully flushed %d trades to Postgres.", len(to_flush))
            except Exception as e:
                logger.error("Error flushing trades to PostgreSQL: %s", e)
                # In case of database failure, log the error but avoid crashing the collector.
                # Do not put the items back in the buffer to prevent memory leakage/stale retry loops.

    async def close(self) -> None:
        """Gracefully flush remaining buffer and close the database connection pool."""
        if self._pool is not None:
            logger.info("Flushing final trades to PostgreSQL…")
            await self.flush()
            logger.info("Closing PostgreSQL connection pool…")
            await self._pool.close()
            self._pool = None
            logger.info("PostgreSQL trade writer closed.")
