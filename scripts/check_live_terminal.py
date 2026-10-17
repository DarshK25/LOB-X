#!/usr/bin/env python3
"""Bounded public-feed smoke test: no storage, credentials, or real orders."""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from io import StringIO
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1] / "backend" / "python"
sys.path.insert(0, str(_BACKEND))

from lobx.terminal.dashboard import LiveDashboard
from rich.console import Console


async def check(seconds: int, reconnect: bool) -> None:
    console = Console(file=StringIO(), width=140, height=38, color_system=None)
    dashboard = LiveDashboard("BTCUSDT", bucket_s=1, console=console)
    task = asyncio.create_task(dashboard.run())
    try:
        await asyncio.sleep(seconds)
        generation = dashboard.collector.depth_mgr.sync_generation
        if reconnect:
            if not dashboard.collector.depth_mgr.is_synced:
                raise RuntimeError("Initial L2 synchronization failed")
            await dashboard.collector.force_disconnect()
            await asyncio.sleep(seconds)
            if dashboard.collector.depth_mgr.sync_generation <= generation:
                raise RuntimeError("Forced reconnect did not rebuild the book")
        snapshot = dashboard.signals.snapshot(dashboard._exchange_now_ms())
        if dashboard._run_error:
            raise RuntimeError(dashboard._run_error)
        if not snapshot.trade_count or not dashboard._book_ready(snapshot):
            raise RuntimeError("Expected observed trades and fresh synchronized L2")
        print({
            "ws": dashboard.collector.ws_status,
            "depth_events": dashboard.depth_events,
            "trades": snapshot.trade_count,
            "candles": len(dashboard.chart.candles),
            "spread": snapshot.spread,
            "base_volume": snapshot.total_volume,
            "sync_generations": dashboard.collector.depth_mgr.sync_generation,
            "reconnect_checked": reconnect,
        })
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=int, default=15,
                        help="Wait per connection (reconnect doubles this duration)")
    parser.add_argument("--reconnect", action="store_true")
    args = parser.parse_args()
    if args.seconds <= 0:
        parser.error("seconds must be positive")
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    start = time.monotonic()
    asyncio.run(check(args.seconds, args.reconnect))
    print(f"Public-feed smoke passed in {time.monotonic() - start:.1f}s; resources closed.")


if __name__ == "__main__":
    main()
