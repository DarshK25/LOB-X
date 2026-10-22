"""Market data for backtests: one in-memory format for every source.

Sources:
  * Binance USD-M futures bookTicker + aggTrades windows (data/history,
    produced by scripts/fetch_history.py)
  * Spot L2 captures written by the live collector (data/raw/depth_*.parquet
    + trades_*.parquet), reduced to top-of-book with the same LocalOrderBook
    the live system uses

Events are merged by timestamp with trades before book updates at equal
timestamps: Binance prints the trade, then the book update that reflects it.
Processing in that order lets the queue model subtract the traded size from
the level change instead of counting it twice.
"""
from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[4]
HISTORY_DIR = ROOT / "data" / "history"
RAW_DIR = ROOT / "data" / "raw"


@dataclass
class MarketData:
    name: str
    venue: str
    tick_size: float
    book_ts: np.ndarray
    bid: np.ndarray
    bid_qty: np.ndarray
    ask: np.ndarray
    ask_qty: np.ndarray
    trade_ts: np.ndarray
    trade_px: np.ndarray
    trade_qty: np.ndarray
    trade_ibm: np.ndarray
    meta: dict = field(default_factory=dict)

    @property
    def mids(self) -> np.ndarray:
        return 0.5 * (self.bid + self.ask)

    @property
    def start(self) -> int:
        return int(min(self.book_ts[0], self.trade_ts[0] if len(self.trade_ts) else self.book_ts[0]))

    @property
    def end(self) -> int:
        return int(max(self.book_ts[-1], self.trade_ts[-1] if len(self.trade_ts) else self.book_ts[-1]))

    @property
    def hours(self) -> float:
        return (self.end - self.start) / 3_600_000

    def slice(self, start_ms: int, end_ms: int, name: str | None = None) -> "MarketData":
        b0, b1 = np.searchsorted(self.book_ts, [start_ms, end_ms])
        t0, t1 = np.searchsorted(self.trade_ts, [start_ms, end_ms])
        return MarketData(
            name=name or f"{self.name}[{start_ms}-{end_ms}]", venue=self.venue,
            tick_size=self.tick_size,
            book_ts=self.book_ts[b0:b1], bid=self.bid[b0:b1], bid_qty=self.bid_qty[b0:b1],
            ask=self.ask[b0:b1], ask_qty=self.ask_qty[b0:b1],
            trade_ts=self.trade_ts[t0:t1], trade_px=self.trade_px[t0:t1],
            trade_qty=self.trade_qty[t0:t1], trade_ibm=self.trade_ibm[t0:t1], meta=dict(self.meta))

    def split(self, frac: float = 0.5) -> tuple["MarketData", "MarketData"]:
        cut = int(self.start + (self.end - self.start) * frac)
        return (self.slice(self.start, cut, f"{self.name}:in-sample"),
                self.slice(cut, self.end + 1, f"{self.name}:out-of-sample"))

    def events(self) -> Iterator[tuple]:
        """Yield ('T', ts, px, qty, ibm) and ('B', ts, bid, bq, ask, aq) in time order."""
        bts, bb, bq, ba, baq = (self.book_ts.tolist(), self.bid.tolist(), self.bid_qty.tolist(),
                                self.ask.tolist(), self.ask_qty.tolist())
        tts, tp, tq, ti = (self.trade_ts.tolist(), self.trade_px.tolist(),
                           self.trade_qty.tolist(), self.trade_ibm.tolist())
        i = j = 0
        nb, nt = len(bts), len(tts)
        while i < nb or j < nt:
            if j < nt and (i >= nb or tts[j] <= bts[i]):
                yield ("T", tts[j], tp[j], tq[j], ti[j])
                j += 1
            else:
                yield ("B", bts[i], bb[i], bq[i], ba[i], baq[i])
                i += 1


def _from_frames(name: str, venue: str, tick: float, book: pd.DataFrame,
                 trades: pd.DataFrame, meta: dict) -> MarketData:
    book = book[(book.ask > book.bid)]
    return MarketData(
        name=name, venue=venue, tick_size=tick,
        book_ts=book.event_time.to_numpy(np.int64), bid=book.bid.to_numpy(float),
        bid_qty=book.bid_qty.to_numpy(float), ask=book.ask.to_numpy(float),
        ask_qty=book.ask_qty.to_numpy(float),
        trade_ts=trades.event_time.to_numpy(np.int64), trade_px=trades.price.to_numpy(float),
        trade_qty=trades.qty.to_numpy(float), trade_ibm=trades.is_buyer_maker.to_numpy(bool),
        meta=meta)


def infer_tick(prices: np.ndarray) -> float:
    diffs = np.diff(np.unique(np.round(prices[: 200_000], 8)))
    diffs = diffs[diffs > 1e-9]
    return float(round(diffs.min(), 8)) if len(diffs) else 0.01


def _history_entry(label_or_file: str) -> dict:
    manifest = json.loads((HISTORY_DIR / "manifest.json").read_text())
    for w in manifest["windows"]:
        if label_or_file in (w["label"], w["book_file"]):
            return w
    raise KeyError(f"no history window {label_or_file!r}; run scripts/fetch_history.py")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def verify_history(label_or_file: str) -> dict:
    """Verify files against the committed manifest before a long study."""
    w = _history_entry(label_or_file)
    checked = {}
    for kind, file_key, hash_key in (
        ("book", "book_file", "book_sha256"), ("trades", "trades_file", "trades_sha256")
    ):
        path = HISTORY_DIR / w[file_key]
        actual = _sha256(path) if path.exists() else None
        expected = w.get(hash_key)
        checked[kind] = {"file": path.name, "exists": path.exists(), "sha256": actual,
                         "expected": expected, "ok": actual is not None and actual == expected}
    checked["ok"] = all(x["ok"] for x in checked.values())
    return checked


def load_history(label_or_file: str, verify: bool = False) -> MarketData:
    """Load a manifest window; optionally verify SHA-256 first."""
    w = _history_entry(label_or_file)
    if verify:
        check = verify_history(label_or_file)
        if not check["ok"]:
            raise ValueError(f"history integrity check failed: {check}")
    book = pd.read_parquet(HISTORY_DIR / w["book_file"])
    trades = pd.read_parquet(HISTORY_DIR / w["trades_file"])
    tick = infer_tick(book.bid.to_numpy())
    start_date, end_date = w.get("start_date", w["date"]), w.get("end_date", w["date"])
    dates = start_date if start_date == end_date else f"{start_date}..{end_date}"
    name = f"{w['label']} {dates} {w['start_utc']}-{w['end_utc']}"
    return _from_frames(name, w["venue"], tick, book, trades, w)


def list_history() -> list[str]:
    path = HISTORY_DIR / "manifest.json"
    if not path.exists():
        return []
    return [w["label"] for w in json.loads(path.read_text())["windows"]]


def load_capture(depth_path: str | Path, trades_path: str | Path, name: str | None = None) -> MarketData:
    """Reduce a collector L2 capture to top-of-book events.

    The capture must start with the persisted REST snapshot (the collector
    writes it as ordinary depth rows), otherwise the book would be wrong.
    """
    from lobx.market_data.depth_stream_manager import LocalOrderBook

    depth = pd.read_parquet(depth_path, columns=["event_time", "side", "price", "qty"])
    trades = pd.read_parquet(trades_path, columns=["event_time", "trade_id", "price", "qty",
                                                   "is_buyer_maker"])
    if depth.empty:
        raise ValueError("empty depth capture")
    depth = depth.sort_values("event_time", kind="stable")
    book = LocalOrderBook()
    ts_arr = depth.event_time.to_numpy(np.int64)
    sides = depth.side.to_numpy()
    prices = depth.price.to_numpy(float)
    qtys = depth.qty.to_numpy(float)
    boundaries = np.flatnonzero(np.diff(ts_arr)) + 1
    starts = np.concatenate([[0], boundaries])
    ends = np.concatenate([boundaries, [len(ts_arr)]])
    if ends[0] - starts[0] < 100:
        raise ValueError("capture has no persisted initial snapshot")
    rows = []
    last = None
    for s, e in zip(starts.tolist(), ends.tolist()):
        for k in range(s, e):
            book.apply_levels(sides[k], [(prices[k], qtys[k])])
        bb, ba = book.best_bid(), book.best_ask()
        if bb is None or ba is None or bb[0] >= ba[0]:
            continue
        top = (bb[0], bb[1], ba[0], ba[1])
        if top != last:
            rows.append((int(ts_arr[s]), *top))
            last = top
    frame = pd.DataFrame(rows, columns=["event_time", "bid", "bid_qty", "ask", "ask_qty"])
    trades = trades.sort_values(["event_time", "trade_id"], kind="stable")
    tick = infer_tick(frame.bid.to_numpy())
    return _from_frames(name or Path(depth_path).stem.replace("depth_", "spot "), "Binance spot",
                        tick, frame, trades, {"depth_file": str(depth_path),
                                              "trades_file": str(trades_path)})


def list_captures(min_bytes: int = 200_000) -> list[tuple[Path, Path]]:
    out = []
    for d in sorted(RAW_DIR.glob("depth_*.parquet")):
        t = d.with_name(d.name.replace("depth_", "trades_", 1))
        if t.exists() and d.stat().st_size >= min_bytes:
            out.append((d, t))
    return out
