"""
test_postgres_writer.py — Unit tests for PostgresTradeWriter.
Uses mock context managers to simulate asyncpg connection pool behavior without requiring a running database.
"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from lobx.market_data.trade_stream import TradeTick
from lobx.storage.postgres_writer import PostgresTradeWriter


class AsyncContextManagerMock:
    """A helper to cleanly mock asynchronous context managers in tests."""
    def __init__(self, return_value: any) -> None:
        self.return_value = return_value

    async def __aenter__(self) -> any:
        return self.return_value

    async def __aexit__(self, exc_type: any, exc_val: any, exc_tb: any) -> None:
        pass


def test_initial_state() -> None:
    # URL not provided and not in env -> should be disabled
    with patch.dict(os.environ, {}, clear=True):
        writer = PostgresTradeWriter(database_url=None)
        assert not writer.is_enabled

    # URL provided -> enabled
    writer = PostgresTradeWriter(database_url="postgresql://localhost/db")
    assert writer.is_enabled


@pytest.mark.asyncio
async def test_connect_handles_failure_gracefully() -> None:
    # Simulate connection pool failure
    with patch("asyncpg.create_pool", new_callable=AsyncMock) as mock_create_pool:
        mock_create_pool.side_effect = Exception("Connection refused")
        
        writer = PostgresTradeWriter(database_url="postgresql://localhost/db")
        assert writer.is_enabled
        
        # Connect should catch the exception, log warning, and disable itself
        await writer.connect()
        assert not writer.is_enabled
        mock_create_pool.assert_called_once()


@pytest.mark.asyncio
async def test_ensure_schema_and_batch_write_flush() -> None:
    mock_conn = AsyncMock()
    mock_pool = MagicMock()  # MagicMock, not AsyncMock, because acquire() is a sync call
    
    # Configure mock pool to return our mock transaction and connection context
    mock_pool.acquire = MagicMock(return_value=AsyncContextManagerMock(mock_conn))
    mock_conn.transaction = MagicMock(return_value=AsyncContextManagerMock(None))

    with patch("asyncpg.create_pool", new_callable=AsyncMock) as mock_create_pool:
        mock_create_pool.return_value = mock_pool
        
        writer = PostgresTradeWriter(database_url="postgresql://localhost/db")
        await writer.connect()
        
        assert writer.is_enabled
        # verify schema creation DDL was executed
        mock_conn.execute.assert_called_once()
        
        # Write some ticks
        tick1 = TradeTick(
            event_time=1722567890000,
            trade_id=1001,
            price=67500.0,
            qty=0.01,
            is_buyer_maker=True,
            symbol="BTCUSDT"
        )
        
        tick2 = TradeTick(
            event_time=1722567891000,
            trade_id=1002,
            price=67501.0,
            qty=0.02,
            is_buyer_maker=False,
            symbol="BTCUSDT"
        )
        
        # Buffer write (does not trigger flush immediately since limit is 200)
        writer.write(tick1)
        writer.write(tick2)
        assert len(writer._buffer) == 2
        mock_conn.executemany.assert_not_called()
        
        # Explicit flush
        await writer.flush()
        assert len(writer._buffer) == 0
        mock_conn.executemany.assert_called_once()
        
        # Verify columns passed to executemany match schema values
        args, _ = mock_conn.executemany.call_args
        rows = args[1]
        assert len(rows) == 2
        assert rows[0] == (1722567890000, 1001, 67500.0, 0.01, 675.0, True, "sell", "BTCUSDT")
        assert rows[1] == (1722567891000, 1002, 67501.0, 0.02, 1350.02, False, "buy", "BTCUSDT")


@pytest.mark.asyncio
async def test_close_flushes_and_closes_pool() -> None:
    mock_conn = AsyncMock()
    mock_pool = MagicMock()  # MagicMock, not AsyncMock
    mock_pool.acquire = MagicMock(return_value=AsyncContextManagerMock(mock_conn))
    mock_conn.transaction = MagicMock(return_value=AsyncContextManagerMock(None))
    mock_pool.close = AsyncMock()

    with patch("asyncpg.create_pool", new_callable=AsyncMock) as mock_create_pool:
        mock_create_pool.return_value = mock_pool
        
        writer = PostgresTradeWriter(database_url="postgresql://localhost/db")
        await writer.connect()
        
        tick = TradeTick(
            event_time=1722567890000,
            trade_id=1001,
            price=67500.0,
            qty=0.01,
            is_buyer_maker=True,
            symbol="BTCUSDT"
        )
        writer.write(tick)
        
        # Close should trigger flush and then close connection pool
        await writer.close()
        mock_conn.executemany.assert_called_once()
        mock_pool.close.assert_called_once()
