"""
run_collector.py — record live Binance L2 depth diffs and trades to Parquet.

The local book follows Binance's documented sync procedure (REST snapshot +
buffered diff stream, sequence-checked, resynced on any gap). Optionally every
applied level change is mirrored into the C++ book (requires lobx_cpp).

USAGE
  python scripts/run_collector.py --no-engine-mirror
  python scripts/run_collector.py --symbol ETHUSDT --duration-seconds 7200
  python scripts/run_collector.py --log-level WARNING --log-file data/raw/collector.log

OUTPUT (data/raw/, rotated at UTC midnight)
  depth_YYYY-MM-DD.parquet    one row per price-level change
  trades_YYYY-MM-DD.parquet   one row per trade

Row groups are flushed on every heartbeat, but the Parquet footer is only
written when the file is closed (rotation or shutdown), so a file is readable
after the collector stops. Replay a capture with
``lobx.backtest.data.load_capture(depth_path, trades_path)``.
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
        description="LOB-X live Binance data collector"
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
        enable_storage=True,
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
