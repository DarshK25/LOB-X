"""Ordered event log of a live paper session, for later replay and analysis.

One gzip'd JSON-lines file per session, written in the exact order events
reached the simulator:

  {"k": "meta", ...}                              symbol, venue, fees, sizing, start time
  {"k": "book", "ts", "bid", "bq", "ask", "aq"}   top of book (only when it changes)
  {"k": "trade", "ts", "px", "qty", "ibm"}        every public trade
  {"k": "calibration", "ts", "calib", "strategies", "config"}
  {"k": "order" | "cancel" | "reject" | "fill", "acct", ...}   account events
  {"k": "hawkes", "ts", "buy", "sell"}            online Hawkes refits
  {"k": "end", "ts", "summary"}

Market events cover the warm-up too, so a replay can recalibrate from
scratch. Lines are flushed about once a second so a crash loses little.
"""
from __future__ import annotations

import gzip
import json
import time
from pathlib import Path


class SessionRecorder:
    def __init__(self, path: str | Path, meta: dict, flush_s: float = 1.0) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = gzip.open(self.path, "wt", encoding="utf-8", compresslevel=6)
        self._flush_s = flush_s
        self._next_flush = time.monotonic() + flush_s
        self.events = 0
        self.write("meta", **meta)

    def write(self, kind: str, **fields) -> None:
        if self._fh is None:
            return
        self._fh.write(json.dumps({"k": kind, **fields}, separators=(",", ":"), default=_default))
        self._fh.write("\n")
        self.events += 1
        now = time.monotonic()
        if now >= self._next_flush:
            self._fh.flush()
            self._next_flush = now + self._flush_s

    def account_listener(self, kind: str, fields: dict) -> None:
        self.write(kind, **fields)

    def close(self, summary: dict | None = None, ts: int | None = None) -> None:
        if self._fh is None:
            return
        self.write("end", ts=ts, summary=summary or {})
        self._fh.close()
        self._fh = None


def read_session(path: str | Path):
    """Yield the events of a recorded session in order.

    A session that was killed leaves a gzip stream without its trailer; every
    complete line before that point is still returned.
    """
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        try:
            for line in fh:
                if line.endswith("\n"):
                    yield json.loads(line)
        except (EOFError, gzip.BadGzipFile):
            return


def _default(o):
    if hasattr(o, "item"):
        return o.item()
    if hasattr(o, "tolist"):
        return o.tolist()
    raise TypeError(type(o))
