"""Storage sub-package — Parquet writers and database stubs."""
from __future__ import annotations

from lobx.storage.parquet_writer import (
    RotatingParquetWriter,
    TRADE_SCHEMA,
    DEPTH_SCHEMA,
    SNAPSHOT_SCHEMA,
)

__all__ = [
    "RotatingParquetWriter",
    "TRADE_SCHEMA",
    "DEPTH_SCHEMA",
    "SNAPSHOT_SCHEMA",
    # Stubs below — implement in Phase 3
    "PostgresStorage",
    "RedisStorage",
]


class PostgresStorage:
    """Stub. Implement with SQLAlchemy async engine in Phase 3."""

    async def save_trade(self, trade_data: dict) -> None:
        raise NotImplementedError

    async def get_trades(self, limit: int = 100) -> list[dict]:
        raise NotImplementedError


class RedisStorage:
    """Stub. Implement with redis-py async client in Phase 3."""

    async def publish_book_snapshot(self, snapshot: dict) -> None:
        raise NotImplementedError

    async def get_latest_snapshot(self) -> dict | None:
        raise NotImplementedError
