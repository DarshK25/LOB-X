#!/usr/bin/env python3
"""Download real Binance USD-M futures top-of-book + trades and store compact windows.

Source: https://data.binance.vision (official Binance public archive)
  * bookTicker  - every best bid/ask change with displayed size (L1 tick-by-tick)
  * aggTrades   - every aggressive trade with aggressor side

Binance stopped publishing futures bookTicker after March 2024, so the default
windows are a calm session (2024-03-30) and the 2024-03-05 crash (BTC fell
from ~69k to ~59k in about an hour). Each window is a few hours, which keeps
the repository-local dataset well under 200 MB while still containing millions
of book updates and hundreds of thousands of trades.

Output (data/history/):
  <SYMBOL>_<DATE>_<HHMM>-<HHMM>_book.parquet    event_time, bid, bid_qty, ask, ask_qty
  <SYMBOL>_<DATE>_<HHMM>-<HHMM>_trades.parquet  event_time, trade_id, price, qty, is_buyer_maker
  manifest.json                                 rows, sha256, time range, source URLs

Usage:
  python scripts/fetch_history.py                    # default windows
  python scripts/fetch_history.py --window 2024-03-02 00:00 04:00 --label calm2
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
import time
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "cache"
OUT = ROOT / "data" / "history"
BASE = "https://data.binance.vision/data/futures/um/daily/{kind}/{sym}/{sym}-{kind}-{date}.zip"

DEFAULT_WINDOWS = [
    ("2024-03-30", "08:00", "12:00", "calm"),
    ("2024-03-05", "13:00", "17:00", "crash"),
]


def _download(url: str, dest: Path) -> Path:
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")
    req = urllib.request.Request(url, headers={"User-Agent": "lobx-research/1.0"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=60) as resp, open(tmp, "wb") as fh:
        total = int(resp.headers.get("Content-Length", 0))
        done = 0
        while chunk := resp.read(1 << 20):
            fh.write(chunk)
            done += len(chunk)
            if total:
                print(f"\r  {dest.name}: {done / 1e6:7.1f}/{total / 1e6:.1f} MB", end="", flush=True)
    tmp.replace(dest)
    print(f"  ({time.time() - t0:.0f}s)")
    return dest


def _ms(date: str, hhmm: str) -> int:
    dt = datetime.strptime(f"{date} {hhmm}", "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _read_window(zip_path: Path, time_col: str, start_ms: int, end_ms: int,
                 usecols: list[str]) -> pd.DataFrame:
    """Stream the CSV inside the zip in chunks and keep only [start, end)."""
    keep = []
    with zipfile.ZipFile(zip_path) as zf:
        name = zf.namelist()[0]
        with zf.open(name) as raw:
            head = raw.readline().decode()
            has_header = not head[:1].isdigit()
            stream = io.TextIOWrapper(raw, encoding="utf-8")
            names = head.strip().split(",") if has_header else None
            if not has_header:
                stream = io.TextIOWrapper(zf.open(name), encoding="utf-8")
            for chunk in pd.read_csv(stream, names=names, header=None, chunksize=1_000_000,
                                     usecols=usecols):
                t = chunk[time_col]
                if t.iloc[-1] < start_ms:
                    continue
                keep.append(chunk[(t >= start_ms) & (t < end_ms)])
                if t.iloc[-1] >= end_ms:
                    break
    return pd.concat(keep, ignore_index=True) if keep else pd.DataFrame(columns=usecols)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def _write(df: pd.DataFrame, path: Path) -> None:
    pq.write_table(pa.Table.from_pandas(df, preserve_index=False), path, compression="zstd")


def _daily_ranges(start_date: str, start: str, end_date: str,
                  end: str) -> list[tuple[str, int, int]]:
    """UTC daily archive slices covering the half-open requested interval."""
    begin = datetime.strptime(f"{start_date} {start}", "%Y-%m-%d %H:%M").replace(
        tzinfo=timezone.utc)
    finish = datetime.strptime(f"{end_date} {end}", "%Y-%m-%d %H:%M").replace(
        tzinfo=timezone.utc)
    if finish <= begin:
        raise ValueError("range end must be after its start")
    out = []
    day = datetime.combine(begin.date(), datetime.min.time(), tzinfo=timezone.utc)
    while day < finish:
        nxt = day + timedelta(days=1)
        lo, hi = max(begin, day), min(finish, nxt)
        if lo < hi:
            out.append((day.date().isoformat(), int(lo.timestamp() * 1000),
                        int(hi.timestamp() * 1000)))
        day = nxt
    return out


def fetch_range(symbol: str, start_date: str, start: str, end_date: str, end: str,
                label: str, keep_zip: bool) -> dict:
    ranges = _daily_ranges(start_date, start, end_date, end)
    same_day = start_date == end_date
    date_part = start_date if same_day else f"{start_date}_{end_date}"
    stem = f"{symbol}_{date_part}_{start.replace(':', '')}-{end.replace(':', '')}"
    OUT.mkdir(parents=True, exist_ok=True)
    entry = {"label": label, "symbol": symbol, "date": start_date,
             "start_date": start_date, "end_date": end_date, "start_utc": start,
             "end_utc": end, "venue": "Binance USD-M futures"}

    books, trades_parts, sources = [], [], []
    for day, start_ms, end_ms in ranges:
        url = BASE.format(kind="bookTicker", sym=symbol, date=day)
        zp = _download(url, CACHE / Path(url).name)
        books.append(_read_window(
            zp, "transaction_time", start_ms, end_ms,
            ["best_bid_price", "best_bid_qty", "best_ask_price", "best_ask_qty",
             "transaction_time"]))
        sources.append(url)
        if not keep_zip:
            zp.unlink(missing_ok=True)

        url_t = BASE.format(kind="aggTrades", sym=symbol, date=day)
        zt = _download(url_t, CACHE / Path(url_t).name)
        trades_parts.append(_read_window(
            zt, "transact_time", start_ms, end_ms,
            ["agg_trade_id", "price", "quantity", "transact_time", "is_buyer_maker"]))
        sources.append(url_t)
        if not keep_zip:
            zt.unlink(missing_ok=True)

    book = pd.concat(books, ignore_index=True)
    book = book.rename(columns={"transaction_time": "event_time", "best_bid_price": "bid",
                                "best_bid_qty": "bid_qty", "best_ask_price": "ask",
                                "best_ask_qty": "ask_qty"})
    book = book[["event_time", "bid", "bid_qty", "ask", "ask_qty"]].sort_values(
        "event_time", kind="stable")
    # Consecutive identical top-of-book rows carry no information.
    changed = book[["bid", "bid_qty", "ask", "ask_qty"]].ne(
        book[["bid", "bid_qty", "ask", "ask_qty"]].shift()).any(axis=1)
    book = book[changed].reset_index(drop=True)
    if book.empty:
        raise ValueError(f"{label}: archive contained no book rows in requested interval")
    book_path = OUT / f"{stem}_book.parquet"
    _write(book, book_path)

    trades = pd.concat(trades_parts, ignore_index=True)
    trades = trades.rename(columns={"agg_trade_id": "trade_id", "quantity": "qty",
                                    "transact_time": "event_time"})
    trades["is_buyer_maker"] = trades["is_buyer_maker"].astype(str).str.lower().eq("true")
    trades = trades[["event_time", "trade_id", "price", "qty", "is_buyer_maker"]].sort_values(
        ["event_time", "trade_id"], kind="stable").reset_index(drop=True)
    if trades.empty:
        raise ValueError(f"{label}: archive contained no trades in requested interval")
    trades_path = OUT / f"{stem}_trades.parquet"
    _write(trades, trades_path)

    entry.update({
        "book_file": book_path.name, "book_rows": len(book), "book_sha256": _sha256(book_path),
        "trades_file": trades_path.name, "trade_rows": len(trades),
        "trades_sha256": _sha256(trades_path),
        "price_min": float(trades.price.min()), "price_max": float(trades.price.max()),
        "base_volume": float(trades.qty.sum()),
        "sources": sources,
    })
    print(f"  {label}: {len(book):,} book rows, {len(trades):,} trades, "
          f"price {entry['price_min']:,.1f}-{entry['price_max']:,.1f}")
    return entry


def fetch_window(symbol: str, date: str, start: str, end: str, label: str,
                 keep_zip: bool) -> dict:
    """Backward-compatible one-day wrapper."""
    return fetch_range(symbol, date, start, date, end, label, keep_zip)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--window", nargs=3, action="append", metavar=("DATE", "START", "END"))
    p.add_argument("--range", nargs=4, action="append",
                   metavar=("START_DATE", "START", "END_DATE", "END"),
                   help="multi-day UTC half-open range (repeatable)")
    p.add_argument("--label", action="append")
    p.add_argument("--keep-zip", action="store_true")
    args = p.parse_args()

    requested = []
    if args.window:
        requested.extend((d, s, d, e) for d, s, e in args.window)
    if args.range:
        requested.extend(tuple(x) for x in args.range)
    if not requested:
        requested = [(d, s, d, e) for d, s, e, _label in DEFAULT_WINDOWS]
        default_labels = [x[3] for x in DEFAULT_WINDOWS]
    else:
        default_labels = [f"w{i}" for i in range(len(requested))]
    labels = args.label or default_labels
    if len(labels) != len(requested):
        p.error("provide exactly one --label per --window/--range")

    manifest_path = OUT / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {"windows": []}
    for (start_date, start, end_date, end), label in zip(requested, labels):
        print(f"[{label}] {args.symbol} {start_date} {start} - {end_date} {end} UTC")
        try:
            entry = fetch_range(args.symbol, start_date, start, end_date, end, label, args.keep_zip)
        except Exception as exc:
            print(f"  FAILED: {exc}", file=sys.stderr)
            continue
        manifest["windows"] = [w for w in manifest["windows"] if w["book_file"] != entry["book_file"]]
        manifest["windows"].append(entry)
        manifest_path.write_text(json.dumps(manifest, indent=2))
        print(f"manifest: {manifest_path}")


if __name__ == "__main__":
    sys.exit(main())
