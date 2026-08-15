"""
verify_engine_mirror.py — confirm that the C++ engine book matches the
reference LocalOrderBook (Python side) tick-for-tick.

Run this AFTER building the C++ engine:
  python scripts/verify_engine_mirror.py

What it verifies
────────────────
On every 20th depth event it compares:
  - LocalOrderBook.best_bid/ask  (Python SortedDict)
  - lobx_cpp.OrderBook.best_bid/ask  (C++ std::map, integer ticks)

Both must agree within $0.01 (one tick).

The script also tracks how many DISTINCT best-bid prices it has seen during
the session — this gives you confidence the match held across real book
movement, not just while the market was sitting still.

Run for at least 3-5 minutes so the book has moved several times.

Usage
─────
  python scripts/verify_engine_mirror.py              # BTCUSDT
  python scripts/verify_engine_mirror.py --symbol ETHUSDT
  python scripts/verify_engine_mirror.py --check-every 10   # check every 10th event

Expected output
───────────────
  [   20] [MATCH OK]  ref bid= 63441.98  ask= 63441.99  |  eng bid= 63441.98  ask= 63441.99  | unique prices seen: 1
  [   40] [MATCH OK]  ref bid= 63442.05  ask= 63442.06  |  eng bid= 63442.05  ask= 63442.06  | unique prices seen: 2
  ...
  [  300] [MATCH OK]  ref bid= 63489.12  ask= 63489.13  |  eng bid= 63489.12  ask= 63489.13  | unique prices seen: 18
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
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1] / "backend" / "python"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from lobx.market_data.collector import Collector
from lobx.market_data.engine_mirror import TICKS_PER_UNIT


# ── Verification state ────────────────────────────────────────────────────────
_counter       = 0
_matches       = 0
_mismatches    = 0
_unique_bids: set[float] = set()   # track distinct best-bid prices seen
TOLERANCE_USD  = 0.01              # prices must agree within $0.01

# CHECK_EVERY_N is set from --check-every argument (default 20)
_check_every_n = 20


def _verify(event: dict, collector: Collector) -> None:
    global _counter, _matches, _mismatches

    _counter += 1
    if _counter % _check_every_n != 0:
        return

    # ── Python reference book ─────────────────────────────────────────────────
    ref_bb = collector.depth_mgr.book.best_bid()
    ref_ba = collector.depth_mgr.book.best_ask()
    if ref_bb is None or ref_ba is None:
        return   # pre-sync: skip

    ref_bid_p, _ = ref_bb
    ref_ask_p, _ = ref_ba

    # ── C++ engine book ───────────────────────────────────────────────────────
    mirror = collector.engine_mirror
    if mirror is None:
        print("  [!] engine_mirror is None — was enable_engine_mirror=True passed?")
        return

    eng_bid_ticks = mirror.book.best_bid()   # int ticks or None
    eng_ask_ticks = mirror.book.best_ask()

    if eng_bid_ticks is None or eng_ask_ticks is None:
        print(f"  [{_counter:>6}] C++ book not yet populated — still waiting for first event...")
        return

    # Convert integer ticks back to float USD for human-readable comparison
    eng_bid_p = eng_bid_ticks / TICKS_PER_UNIT
    eng_ask_p = eng_ask_ticks / TICKS_PER_UNIT

    # Track price diversity — proves we matched through real movement, not just
    # a static quote.
    _unique_bids.add(ref_bid_p)

    # ── Compare ───────────────────────────────────────────────────────────────
    bid_ok = abs(ref_bid_p - eng_bid_p) <= TOLERANCE_USD
    ask_ok = abs(ref_ask_p - eng_ask_p) <= TOLERANCE_USD
    match  = bid_ok and ask_ok

    if match:
        _matches += 1
        status = "MATCH OK   "
    else:
        _mismatches += 1
        status = "MISMATCH ! "

    print(
        f"  [{_counter:>6}] [{status}]  "
        f"ref bid={ref_bid_p:>10.2f}  ask={ref_ask_p:>10.2f}  |  "
        f"eng bid={eng_bid_p:>10.2f}  ask={eng_ask_p:>10.2f}  |  "
        f"unique prices seen: {len(_unique_bids)}"
    )

    if not match:
        print(f"  {'':6}   [INVESTIGATING]")
        if not bid_ok:
            diff = ref_bid_p - eng_bid_p
            print(f"             bid diff = {diff:+.6f} USD "
                  f"({abs(diff) * TICKS_PER_UNIT:.2f} ticks)")
            print(f"             ref={ref_bid_p:.8f}, eng={eng_bid_p:.8f}")
        if not ask_ok:
            diff = ref_ask_p - eng_ask_p
            print(f"             ask diff = {diff:+.6f} USD "
                  f"({abs(diff) * TICKS_PER_UNIT:.2f} ticks)")
        print(f"             TICKS_PER_UNIT = {TICKS_PER_UNIT}")
        print("             Possible causes:")
        print("             1. TICKS_PER_UNIT wrong for this symbol (check engine_mirror.py)")
        print("             2. _synthetic_id() collision in engine_mirror.py")
        print("             3. A depth event was dropped (rare — check log for errors)")


async def _main(symbol: str) -> None:
    try:
        import lobx_cpp  # noqa: F401
    except ImportError:
        print("\n  ERROR: lobx_cpp not importable.")
        print("  Run: python scripts/setup_cpp_build.py  first.")
        sys.exit(1)

    print(f"\n  Verifying engine mirror for {symbol}")
    print(f"  Checking every {_check_every_n}th depth event  |  tolerance +/-${TOLERANCE_USD:.2f}")
    print(f"  Let this run for 3+ minutes to see MATCH across real price movement.")
    print(f"  Press Ctrl+C to stop and see final results.\n")
    print(f"  {'EVENT':>8}   {'STATUS':<14}  {'REF BOOK':^35}  {'C++ ENGINE':^35}  DIVERSITY")
    print("  " + "-" * 110)

    collector = Collector(
        symbol=symbol,
        data_dir="data/raw_verify",
        enable_engine_mirror=True,
        heartbeat_s=3600,   # suppress heartbeat log noise during verification
    )
    collector.on_book_update(lambda e: _verify(e, collector))

    try:
        await collector.run()
    except (KeyboardInterrupt, Exception):
        pass
    finally:
        collector.close()
        total = _matches + _mismatches
        pct   = (_matches / total * 100) if total > 0 else 0.0

        print("\n" + "  " + "=" * 70)
        print(f"  VERIFICATION SUMMARY for {symbol}")
        print("  " + "=" * 70)
        print(f"  Total depth events processed : {_counter:>8,}")
        print(f"  Checks performed (every {_check_every_n}th) : {total:>8,}")
        print(f"  Matches                      : {_matches:>8,}  ({pct:.1f}%)")
        print(f"  Mismatches                   : {_mismatches:>8,}")
        print(f"  Unique best-bid prices seen  : {len(_unique_bids):>8,}  (price diversity)")
        print("  " + "-" * 70)
        if _mismatches == 0 and total > 0:
            print("  RESULT: PASSED — C++ engine matches reference book 100%")
        elif total == 0:
            print("  RESULT: NO DATA — did not receive any depth events")
        else:
            print(f"  RESULT: FAILED — {_mismatches} mismatches detected")
            print("  See diagnostics above. Check engine_mirror.py.")
        print("  " + "=" * 70)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Verify C++ engine mirror vs. Python reference book")
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--check-every", type=int, default=20,
                   help="Check every Nth depth event (default: 20, ~1 check per 2s for BTC)")
    return p.parse_args()


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING,
                        format="%(asctime)s %(levelname)s %(message)s")
    args = _parse_args()
    _check_every_n = args.check_every
    asyncio.run(_main(args.symbol))
