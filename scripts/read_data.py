"""
read_data.py — inspect the Parquet files written by run_collector.py.

Usage
─────
  # After running run_collector.py for at least 30 seconds:
  python scripts/read_data.py

  # Show more rows:
  python scripts/read_data.py --rows 20

  # Read a specific file:
  python scripts/read_data.py --file data/raw/depth_2026-08-02.parquet

What this shows
───────────────
  1. List of all Parquet files and their sizes.
  2. Latest 10 trade rows (price, qty, side, time).
  3. Reconstructed best bid/ask from depth diffs (what the book looked like
     at the time of the last depth event in the file).
  4. Stats: total volume, trade count, update-ID range.

Note on file readability
────────────────────────
pyarrow can only read a Parquet file that has a valid footer — the footer is
written when the writer CLOSES or FLUSHES.  The Collector flushes every 500
rows OR every 30 seconds (heartbeat), so files are readable within ~30 s of
the first events arriving.  If you see "Parquet magic bytes not found", just
wait another 30 s and try again.
"""
from __future__ import annotations

import argparse
import datetime
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


def _ms_to_time(ms: int) -> str:
    return datetime.datetime.fromtimestamp(ms / 1000).strftime("%H:%M:%S.%f")[:-3]


def _safe_read(path: Path):
    """Read a Parquet file, returning None and printing a helpful message on failure."""
    try:
        import pyarrow.parquet as pq
        return pq.read_table(str(path)).to_pandas()
    except Exception as exc:
        print(f"  Cannot read {path.name}: {exc}")
        print("  → Wait 30 s for the next heartbeat flush and try again.")
        return None


def _show_files(data_dir: Path) -> list[Path]:
    """List all Parquet files in data_dir with sizes."""
    files = sorted(data_dir.glob("*.parquet"))
    if not files:
        print(f"\nNo Parquet files found in {data_dir}/")
        print("→ Start run_collector.py and wait at least 30 seconds.")
        return []

    print(f"\n{'FILE':<45}  {'SIZE':>10}  {'ROWS':>8}")
    print("─" * 68)
    for f in files:
        size_kb = f.stat().st_size / 1024
        # Try to get row count without loading all data
        try:
            import pyarrow.parquet as pq
            meta = pq.read_metadata(str(f))
            rows = meta.num_rows
        except Exception:
            rows = -1
        rows_str = str(rows) if rows >= 0 else "?"
        print(f"  {f.name:<43}  {size_kb:>8.1f} KB  {rows_str:>8}")
    return files


def _show_trades(path: Path, n_rows: int) -> None:
    """Display the latest n_rows trade rows."""
    df = _safe_read(path)
    if df is None or df.empty:
        print("  No trade data yet.")
        return

    df["time"] = df["event_time"].apply(_ms_to_time)
    df["side"] = df["is_buyer_maker"].apply(
        lambda m: "SELL(hit bid) ▼" if m else " BUY(lift ask) ▲"
    )
    df["notional"] = df["price"] * df["qty"]

    print(f"\n  TRADES — latest {min(n_rows, len(df))} of {len(df)} total rows")
    print(f"  {'TIME':<12} {'SIDE':<18} {'PRICE':>12} {'QTY BTC':>12} {'NOTIONAL USD':>14}")
    print("  " + "─" * 72)
    for _, r in df.tail(n_rows).iterrows():
        print(
            f"  {r['time']:<12} {r['side']:<18} "
            f"{r['price']:>12.2f} {r['qty']:>12.5f} {r['notional']:>14.2f}"
        )

    print(f"\n  Stats:")
    print(f"    Total trades    : {len(df):>8,}")
    print(f"    Buy (lift ask)  : {(~df.is_buyer_maker).sum():>8,}")
    print(f"    Sell (hit bid)  : {df.is_buyer_maker.sum():>8,}")
    print(f"    Total vol (BTC) : {df.qty.sum():>12.4f}")
    print(f"    Price range     : {df.price.min():.2f} — {df.price.max():.2f}")


def _show_depth(path: Path, n_levels: int) -> None:
    """Display reconstructed best bid/ask from depth diffs."""
    df = _safe_read(path)
    if df is None or df.empty:
        print("  No depth data yet.")
        return

    print(f"\n  DEPTH — {len(df):,} level-change rows total")

    # Reconstruct the most recent book state by taking the last qty written
    # for each (side, price) pair.  qty == 0 means "level removed".
    latest = (
        df.sort_values("event_time")
        .groupby(["side", "price"])["qty"]
        .last()
        .reset_index()
    )
    live_bids = (
        latest[(latest.side == "bid") & (latest.qty > 0)]
        .sort_values("price", ascending=False)
        .head(n_levels)
    )
    live_asks = (
        latest[(latest.side == "ask") & (latest.qty > 0)]
        .sort_values("price")
        .head(n_levels)
    )

    best_bid = live_bids.iloc[0] if not live_bids.empty else None
    best_ask = live_asks.iloc[0] if not live_asks.empty else None

    if best_bid is not None and best_ask is not None:
        mid = (best_bid["price"] + best_ask["price"]) / 2
        spread = best_ask["price"] - best_bid["price"]
        print(f"  Best bid : {best_bid['price']:>10.2f}  qty={best_bid['qty']:.5f}")
        print(f"  Best ask : {best_ask['price']:>10.2f}  qty={best_ask['qty']:.5f}")
        print(f"  Mid      : {mid:>10.2f}")
        print(f"  Spread   : {spread:>10.2f}")

    print(f"\n  {'BIDS (best first)':<40}  {'ASKS (best first)':<40}")
    print("  " + "─" * 82)
    bid_rows = live_bids[["price", "qty"]].values.tolist()
    ask_rows = live_asks[["price", "qty"]].values.tolist()
    for i in range(max(len(bid_rows), len(ask_rows))):
        bid_str = f"  {bid_rows[i][0]:>10.2f} × {bid_rows[i][1]:.5f}" if i < len(bid_rows) else ""
        ask_str = f"  {ask_rows[i][0]:>10.2f} × {ask_rows[i][1]:.5f}" if i < len(ask_rows) else ""
        print(f"  {bid_str:<40}  {ask_str:<40}")

    # Update ID range
    print(f"\n  Update ID range : {df.first_update_id.min():,} → {df.final_update_id.max():,}")
    t0 = _ms_to_time(int(df.event_time.min()))
    t1 = _ms_to_time(int(df.event_time.max()))
    print(f"  Time range      : {t0} → {t1}")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Read LOB-X Parquet data files")
    p.add_argument("--data-dir", default="data/raw")
    p.add_argument("--rows", type=int, default=10, help="Number of rows to display")
    p.add_argument("--levels", type=int, default=8, help="Number of price levels to show")
    p.add_argument("--file", default=None, help="Read a specific .parquet file (debug mode)")
    return p.parse_args()


def main() -> None:
    args = _parse_args()

    print("=" * 68)
    print("  LOB-X Data Reader")
    print("=" * 68)

    if args.file:
        # Debug mode: read and dump a single specified file
        path = Path(args.file)
        print(f"\nReading: {path.name}")
        df = _safe_read(path)
        if df is not None:
            print(df.head(args.rows).to_string())
        return

    data_dir = Path(args.data_dir)
    files = _show_files(data_dir)
    if not files:
        return

    # Separate trade and depth files
    trade_files = [f for f in files if f.name.startswith("trades_")]
    depth_files = [f for f in files if f.name.startswith("depth_")]

    # Show the most recent of each
    if trade_files:
        print(f"\n{'─' * 68}")
        print(f"  TRADES — reading {trade_files[-1].name}")
        print("─" * 68)
        _show_trades(trade_files[-1], args.rows)

    if depth_files:
        print(f"\n{'─' * 68}")
        print(f"  DEPTH — reading {depth_files[-1].name}")
        print("─" * 68)
        _show_depth(depth_files[-1], args.levels)

    print(f"\n{'=' * 68}")
    print("  Tip: run again in 30 s to see fresh data.")
    print("  Tip: python scripts/watch_live.py  → see data updating IN REAL TIME.")
    print("=" * 68)


if __name__ == "__main__":
    main()
