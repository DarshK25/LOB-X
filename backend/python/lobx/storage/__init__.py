"""Storage stubs — PostgreSQL and Redis connections."""
from __future__ import annotations


class PostgresStorage:
    """Stub. Implement with SQLAlchemy async engine."""

    async def save_trade(self, trade_data: dict) -> None:
        raise NotImplementedError

    async def get_trades(self, limit: int = 100) -> list[dict]:
        raise NotImplementedError


class RedisStorage:
    """Stub. Implement with redis-py async client."""

    async def publish_book_snapshot(self, snapshot: dict) -> None:
        raise NotImplementedError

    async def get_latest_snapshot(self) -> dict | None:
        raise NotImplementedError
