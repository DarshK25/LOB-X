"""Load and replay sessions recorded by ``lobx.live.recorder.SessionRecorder``.

``replay_session`` reruns a recorded live session event by event: the same
market events in the same order, the recorded calibration applied at the
same point, and the online Hawkes refits applied where they happened. The
simulator is deterministic, so the replayed fills must match the recorded
ones exactly. Any mismatch means the code changed (or a bug), which is what
makes a recording useful for regression tests and for trying strategy
changes on identical market data.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

from lobx.backtest.data import MarketData
from lobx.execution.simulator import ExecutionConfig, MarketSimulator, SimAccount
from lobx.live.recorder import read_session
from lobx.strategy.base import Strategy
from lobx.strategy.features import FeatureEngine, HawkesParams

MARKET = ("book", "trade")
ACCOUNT = ("order", "cancel", "reject", "fill")

SuiteFactory = Callable[[dict, float], list[Strategy]]


@dataclass
class Session:
    path: Path
    meta: dict
    events: list[dict]
    calibration: dict | None = None
    end: dict | None = None
    by_kind: dict[str, int] = field(default_factory=dict)

    def account_events(self, kind: str | None = None) -> list[dict]:
        kinds = ACCOUNT if kind is None else (kind,)
        return [e for e in self.events if e["k"] in kinds]

    def fills(self) -> dict[str, list[dict]]:
        out: dict[str, list[dict]] = {}
        for e in self.account_events("fill"):
            out.setdefault(e["acct"], []).append(e)
        return out

    def market_data(self, trading_only: bool = False) -> MarketData:
        """Market events as a ``MarketData`` (e.g. to backtest other settings on them)."""
        start = self.calibration["ts"] if trading_only and self.calibration else -1
        book = [(e["ts"], e["bid"], e["bq"], e["ask"], e["aq"]) for e in self.events
                if e["k"] == "book" and e["ts"] > start]
        trades = [(e["ts"], e["px"], e["qty"], e["ibm"]) for e in self.events
                  if e["k"] == "trade" and e["ts"] > start]
        b = np.array(book, dtype=float).reshape(-1, 5)
        t = np.array(trades, dtype=float).reshape(-1, 4)
        return MarketData(
            name=f"session {self.path.name}", venue=self.meta.get("venue", ""),
            tick_size=float(self.meta["tick_size"]),
            book_ts=b[:, 0].astype(np.int64), bid=b[:, 1], bid_qty=b[:, 2], ask=b[:, 3],
            ask_qty=b[:, 4], trade_ts=t[:, 0].astype(np.int64), trade_px=t[:, 1],
            trade_qty=t[:, 2], trade_ibm=t[:, 3].astype(bool), meta=dict(self.meta))


def load_session(path: str | Path) -> Session:
    path = Path(path)
    it = read_session(path)
    meta = next(it, None)
    if meta is None or meta.get("k") != "meta":
        raise ValueError(f"{path} is not a recorded session")
    s = Session(path=path, meta=meta, events=[])
    for e in it:
        k = e["k"]
        s.by_kind[k] = s.by_kind.get(k, 0) + 1
        if k == "calibration":
            s.calibration = e
        elif k == "end":
            s.end = e
        s.events.append(e)
    return s


def suite_from_meta(meta: dict) -> SuiteFactory:
    from lobx.backtest import research

    p = meta["suite"]
    return lambda calib, qty: research.strategy_suite(
        calib, float(meta["tick_size"]), gamma_as=p["gamma_as"], gamma_glft=p["gamma_glft"],
        maker_fee=p["maker_fee"], qty=qty,
        alpha_gates=p.get("alpha_gates"))


@dataclass
class Replay:
    accounts: list[SimAccount]
    sim: MarketSimulator
    events: list[dict]


def replay_session(session: Session, build_suite: SuiteFactory | None = None,
                   config: ExecutionConfig | None = None, decision_ms: int | None = None
                   ) -> Replay:
    """Rerun ``session``; pass ``build_suite``/``config`` to try changes on the same data."""
    cal = session.calibration
    if cal is None:
        raise ValueError("session never finished warm-up (no calibration event)")
    build_suite = build_suite or suite_from_meta(session.meta)
    config = config or ExecutionConfig(**cal["config"])
    decision_ms = decision_ms or int(session.meta.get("decision_ms", 100))
    features = FeatureEngine(config.tick_size,
                             event_history=int(session.meta.get("event_history", 20_000)))
    log: list[dict] = []
    sim: MarketSimulator | None = None
    accounts: list[SimAccount] = []
    for e in session.events:
        k = e["k"]
        if k == "book":
            (sim or features).on_book(e["ts"], e["bid"], e["bq"], e["ask"], e["aq"])
        elif k == "trade":
            (sim or features).on_trade(e["ts"], e["px"], e["qty"], e["ibm"])
        elif k == "calibration" and sim is None:
            calib = e["calib"]
            features.set_hawkes(HawkesParams.from_dict(calib["hawkes"]["buy"]),
                                HawkesParams.from_dict(calib["hawkes"]["sell"]))
            strategies = build_suite(calib, e["quote_qty"])
            # Session files store the exact strategy names used at recording
            # time.  Filtering keeps old recordings deterministic when a new
            # candidate is later added to the default suite.
            recorded_names = set(e.get("strategies", ()))
            if recorded_names:
                strategies = [s for s in strategies if s.name() in recorded_names]
            accounts = [SimAccount(s, config) for s in strategies]
            for acc in accounts:
                acc.listener = lambda kind, fields: log.append({"k": kind, **fields})
            sim = MarketSimulator(features, accounts, decision_ms)
        elif k == "hawkes" and sim is not None:
            features.set_hawkes(HawkesParams.from_dict(e["buy"]),
                                HawkesParams.from_dict(e["sell"]), calibrated=True)
    assert sim is not None
    return Replay(accounts=accounts, sim=sim, events=log)


def compare_fills(session: Session, replay: Replay, tol: float = 1e-9) -> dict[str, dict]:
    """Per account: recorded vs replayed fill counts and the first mismatch, if any."""
    recorded = session.fills()
    replayed: dict[str, list[dict]] = {}
    for e in replay.events:
        if e["k"] == "fill":
            replayed.setdefault(e["acct"], []).append(e)
    out = {}
    for name in sorted(set(recorded) | set(replayed) | {a.name for a in replay.accounts}):
        a, b = recorded.get(name, []), replayed.get(name, [])
        first = None
        for i, (x, y) in enumerate(zip(a, b)):
            if (x["ts"] != y["ts"] or x["side"] != y["side"] or abs(x["px"] - y["px"]) > tol
                    or abs(x["qty"] - y["qty"]) > tol):
                first = i
                break
        if first is None and len(a) != len(b):
            first = min(len(a), len(b))
        out[name] = {"recorded": len(a), "replayed": len(b), "match": first is None,
                     "first_mismatch": first}
    return out
