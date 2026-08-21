"""
run_collector.py — main data recording process for Phase 1.

PURPOSE
───────
This script is your RECORDER — it runs continuously and writes every depth
event and trade to Parquet files on disk.  Leave it running in Terminal 1.
Read the data in real time from Terminal 2 using the other scripts.

USAGE
─────
  # Basic — no C++ engine yet (most common case right now):
  python scripts/run_collector.py --no-engine-mirror

  # Full pipeline — after running setup_cpp_build.py successfully:
  python scripts/run_collector.py

  # Different symbol:
  python scripts/run_collector.py --symbol ETHUSDT --no-engine-mirror

  # Custom data directory:
  python scripts/run_collector.py --data-dir data/live --no-engine-mirror

  # Quiet logging (less output, still shows heartbeat):
  python scripts/run_collector.py --no-engine-mirror --log-level WARNING

LOG LINES TO WATCH FOR
──────────────────────
  "Connected to wss://..."              → WebSocket connected
  "[BTCUSDT] Fetching REST snapshot"   → sync started (takes ~1 second)
  "[BTCUSDT] Fully synced."            → book is LIVE; Parquet writes begin
  "[BTCUSDT] heartbeat | bid=..."      → periodic flush + live book state
  "Disconnected ... Retrying in Ns"    → auto-reconnect (normal after 24h)

WHAT GETS WRITTEN
─────────────────
  data/raw/
    trades_YYYY-MM-DD.parquet   — one row per individual trade
    depth_YYYY-MM-DD.parquet    — one row per price-level change

Files rotate at UTC midnight.  New files are created automatically.
Data is readable within 30 s of the first events (heartbeat flush interval).

OTHER TERMINALS
───────────────
  See live data updating in real time:
    python scripts/watch_live.py

  Read what's been collected so far:
    python scripts/read_data.py

  Verify C++ engine matches real book (after C++ build):
    python scripts/verify_engine_mirror.py
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from pathlib import Path

# ── Path setup ────────────────────────────────────────────────────────────────
_BACKEND = Path(__file__).resolve().parents[1] / "backend" / "python"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from lobx.market_data.collector import Collector


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="LOB-X Phase 1 — live Binance data collector"
    )
    p.add_argument("--symbol",   default="BTCUSDT",
                   help="Binance trading pair (default: BTCUSDT)")
    p.add_argument("--data-dir", default="data/raw",
                   help="Parquet output directory (default: data/raw)")
    p.add_argument("--no-engine-mirror", action="store_true",
                   help="Disable C++ engine (use if not built yet)")
    p.add_argument("--log-level", default="INFO",
                   choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    p.add_argument("--heartbeat", type=int, default=30,
                   help="Flush + log interval in seconds (default: 30)")
    p.add_argument("--duration-seconds", type=float, default=None,
                   help="Stop cleanly after this many seconds")
    p.add_argument("--log-file", type=Path, default=None,
                   help="Write collector evidence to this file")
    return p.parse_args()


async def _main(args: argparse.Namespace) -> None:
    collector = Collector(
        symbol=args.symbol,
        data_dir=args.data_dir,
        enable_engine_mirror=not args.no_engine_mirror,
        heartbeat_s=args.heartbeat,
    )

    run_task = asyncio.create_task(collector.run())
    try:
        if args.duration_seconds is None:
            await run_task
        else:
            await asyncio.sleep(args.duration_seconds)
    except KeyboardInterrupt:
        pass
    finally:
        collector.stop()
        run_task.cancel()
        try:
            await run_task
        except asyncio.CancelledError:
            pass
        print("\nShutting down — flushing buffers…")
        await collector.close_async()
        print("Done. Data written to:", args.data_dir)


if __name__ == "__main__":
    args = _parse_args()

    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if args.log_file is not None:
        args.log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(args.log_file, encoding="utf-8"))
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s.%(msecs)03d  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
    )
    for handler in logging.getLogger().handlers:
        handler.formatter.converter = time.gmtime

    asyncio.run(_main(args))
