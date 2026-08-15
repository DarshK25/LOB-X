"""
watch_live.py — real-time terminal display of the live Binance order book.

This is your PRIMARY way to SEE the live data flowing.

What you see
────────────
  One line updates in-place (like a ticker):
    BID  67543.21 ×1.23400  |  ASK  67544.00 ×0.85000  |  MID  67543.61  |  SPREAD  0.79
    LAST TRADE  BUY (lift ask)   0.00142 BTC @ 67544.00

  The bid/ask line refreshes 4× per second (throttled from 10× to avoid
  terminal flooding).  Each real trade prints on its own line below.

How to run
──────────
  # Terminal 1 — data collector (records to Parquet, keeps running):
  python scripts/run_collector.py --no-engine-mirror

  # Terminal 2 — live display (separate WS connection, doesn't interfere):
  python scripts/watch_live.py

  # Or watch a different symbol:
  python scripts/watch_live.py --symbol ETHUSDT

Architecture note
─────────────────
watch_live.py opens its OWN independent WebSocket connection to Binance —
it does NOT share the Collector from run_collector.py.  This is intentional:
  * No dependency on the recording process — you can start/stop the watcher
    independently.
  * Zero risk of interfering with the recording Collector's state.
  * The overhead of a second connection is negligible for Binance's servers.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
import time
from pathlib import Path

# ── Path setup ────────────────────────────────────────────────────────────────
_BACKEND = Path(__file__).resolve().parents[1] / "backend" / "python"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from lobx.market_data.collector import Collector
from lobx.market_data.trade_stream import TradeTick


# ── Display state ─────────────────────────────────────────────────────────────
_last_bid_line_time: float = 0.0
REFRESH_INTERVAL_S: float = 0.25   # max 4 book-state refreshes per second
_stats = {
    "trades": 0,
    "buy_trades": 0,
    "sell_trades": 0,
    "total_volume": 0.0,
    "depth_events": 0,
}


# ── Callbacks ─────────────────────────────────────────────────────────────────

def _on_book_update(event: dict, collector: Collector) -> None:
    """
    Called after every synced depth event.  Throttled to REFRESH_INTERVAL_S.

    We print a single overwriting line (\r, no newline) so the terminal
    shows a live-updating ticker rather than thousands of scrolling lines.
    """
    global _last_bid_line_time
    _stats["depth_events"] += 1

    now = time.monotonic()
    if now - _last_bid_line_time < REFRESH_INTERVAL_S:
        return
    _last_bid_line_time = now

    book = collector.depth_mgr.book
    bb = book.best_bid()
    ba = book.best_ask()
    if bb is None or ba is None:
        return

    bid_p, bid_q = bb
    ask_p, ask_q = ba
    mid = (bid_p + ask_p) / 2.0
    spread = ask_p - bid_p

    # Build the status line.
    line = (
        f"  BID {bid_p:>10.2f} ×{bid_q:<9.5f}│"
        f"  ASK {ask_p:>10.2f} ×{ask_q:<9.5f}│"
        f"  MID {mid:>10.2f}│"
        f"  SPR {spread:>6.2f}│"
        f"  lvl {len(book.bids):>4}b/{len(book.asks):<4}a│"
        f"  trades {_stats['trades']:>5}"
    )

    # \r returns the cursor to column 0 without a newline, overwriting in place.
    print(f"\r{line}", end="", flush=True)


def _on_trade(tick: TradeTick) -> None:
    """
    Called after every individual trade event.  Prints a new line below the
    ticker so trades are visible without wiping the book state line.

    is_buyer_maker == True  → buyer was the passive (maker) side
                              → the SELLER was aggressive (hit the bid)
    is_buyer_maker == False → seller was the passive (maker) side
                              → the BUYER was aggressive (lifted the ask)
    """
    _stats["trades"] += 1
    _stats["total_volume"] += tick.qty
    if tick.is_buyer_maker:
        _stats["sell_trades"] += 1
        side_str = "SELL (hit bid) ▼"
    else:
        _stats["buy_trades"] += 1
        side_str = " BUY (lift ask) ▲"

    # Print on a new line — the next book update will overwrite the ticker line.
    print(
        f"\n  TRADE {side_str}  "
        f"{tick.qty:.5f} BTC @ {tick.price:>10.2f}  "
        f"notional={tick.notional:>10.2f} USD  "
        f"id={tick.trade_id}",
        flush=True,
    )


# ── Entry point ───────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="LOB-X — live order book watcher")
    p.add_argument("--symbol", default="BTCUSDT",
                   help="Binance symbol to watch (default: BTCUSDT)")
    return p.parse_args()


async def _main(args: argparse.Namespace) -> None:
    # Suppress sync/connection log noise so the terminal stays clean.
    logging.getLogger("lobx").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.WARNING)

    collector = Collector(
        symbol=args.symbol,
        data_dir="data/raw_watch",       # separate dir so we don't mix with recorder
        enable_engine_mirror=False,      # watcher doesn't need the C++ engine
        flush_every=10_000,              # we don't care about Parquet for the watcher
        heartbeat_s=3600,                # suppress heartbeat log noise
    )

    # Register callbacks BEFORE run() — they're called during run().
    collector.on_book_update(lambda event: _on_book_update(event, collector))
    collector.on_trade(_on_trade)

    print(f"\n  Watching {args.symbol} on Binance (Ctrl+C to stop)")
    print(f"  Connecting… ", end="", flush=True)

    try:
        await collector.run()
    except (KeyboardInterrupt, Exception):
        pass
    finally:
        collector.close()
        print(f"\n\n  Session summary for {args.symbol}:")
        print(f"    Depth events processed : {_stats['depth_events']:>8,}")
        print(f"    Trades seen            : {_stats['trades']:>8,}")
        print(f"      Buy  (lift ask)  ▲   : {_stats['buy_trades']:>8,}")
        print(f"      Sell (hit bid)   ▼   : {_stats['sell_trades']:>8,}")
        print(f"    Total volume (BTC)     : {_stats['total_volume']:>12.4f}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING,
                        format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(_main(_parse_args()))
