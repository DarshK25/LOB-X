"""Session recording, capital sizing and deterministic replay of a live paper session."""
import asyncio
import gzip
from types import SimpleNamespace

import pytest

from lobx.analytics.calibration import calibrate
from lobx.backtest import research
from lobx.backtest.session import compare_fills, load_session, replay_session, suite_from_meta
from lobx.execution.simulator import ExecutionConfig
from lobx.live.quote_loop import LiveSimRunner
from lobx.live.recorder import SessionRecorder, read_session
from tests.test_analytics import synthetic_market


class FakeBook:
    def __init__(self):
        self.top = None

    def best_bid(self):
        return None if self.top is None else (self.top[0], self.top[1])

    def best_ask(self):
        return None if self.top is None else (self.top[2], self.top[3])


class FakeCollector:
    def __init__(self):
        self.depth_mgr = SimpleNamespace(book=FakeBook())
        self.book_cb = self.trade_cb = None

    def on_book_update(self, cb):
        self.book_cb = cb

    def on_trade(self, cb):
        self.trade_cb = cb

    def push(self, ev):
        if ev[0] == "B":
            self.depth_mgr.book.top = ev[2:]
            self.book_cb({"E": ev[1]})
        else:
            self.trade_cb(SimpleNamespace(event_time=ev[1], price=ev[2], qty=ev[3],
                                          is_buyer_maker=ev[4]))


def test_size_from_capital():
    qty, limit = research.size_from_capital(10_000, 100_000)
    assert qty == pytest.approx(0.01)
    assert limit == pytest.approx(0.1)
    assert research.size_from_capital(1, 100_000)[0] == pytest.approx(1e-5)  # one lot minimum
    with pytest.raises(ValueError):
        research.size_from_capital(0, 100_000)


def test_recorder_survives_truncated_file(tmp_path):
    path = tmp_path / "s.jsonl.gz"
    rec = SessionRecorder(path, {"symbol": "X"})
    rec.write("book", ts=1, bid=1.0, bq=1.0, ask=2.0, aq=1.0)
    rec._fh.flush()
    raw = path.read_bytes()  # no gzip trailer yet: what a killed process leaves behind
    rec.close()
    broken = tmp_path / "broken.jsonl.gz"
    broken.write_bytes(raw)
    kinds = [e["k"] for e in read_session(broken)]
    assert kinds[0] == "meta"
    assert [e["k"] for e in read_session(path)][-1] == "end"
    with gzip.open(path, "rt") as fh:
        assert len(fh.readlines()) == 3


def test_live_session_records_and_replays_exactly(tmp_path):
    data = synthetic_market(seconds=900, seed=11)
    events = list(data.events())
    cut = len(events) // 2
    warm = data.slice(data.start, events[cut][1], "warm")
    meta = {"symbol": "TEST", "venue": "test", "tick_size": data.tick_size, "capital_usd": 5_000,
            "decision_ms": 100, "event_history": 20_000,
            "suite": {"gamma_as": 0.03, "gamma_glft": 1.0, "maker_fee": 0.0,
                      "alpha_gates": {"base": 0.0, "fee_floor": 0.05,
                                      "base_active": False, "fee_floor_active": True}}}
    rec = SessionRecorder(tmp_path / "TEST.jsonl.gz", meta)
    cfg = ExecutionConfig(tick_size=data.tick_size, maker_fee=0.0, taker_fee=0.0)
    col = FakeCollector()
    runner = LiveSimRunner(col, suite_from_meta(meta), cfg, capital_usd=5_000, recorder=rec)
    for ev in events[:cut]:
        col.push(ev)
    runner.start_trading(calibrate(warm), float(warm.mids[-1]))
    gates = {a.name: getattr(a.strategy, "alpha_pause_sigma", None) for a in runner.accounts}
    assert gates["AdaptiveGLFT alpha-gated"] == 0.0
    assert gates["AdaptiveGLFT alpha-gated fee-floor"] == 0.05
    robust = next(a.strategy for a in runner.accounts if a.name == "AdaptiveGLFT robust-gated")
    assert robust.enabled is False
    assert runner.quote_qty == pytest.approx(round(500 / warm.mids[-1], 5))
    assert runner.config.max_position == pytest.approx(5_000 / warm.mids[-1])
    for i, ev in enumerate(events[cut:]):
        col.push(ev)
        if i == len(events[cut:]) // 2:
            asyncio.run(runner._refit())
    runner.close()

    s = load_session(rec.path)
    assert s.calibration is not None and s.end is not None
    assert s.by_kind["book"] + s.by_kind["trade"] == len(events)
    live_fills = {a.name: len(a.fills) for a in runner.accounts}
    assert sum(live_fills.values()) > 0
    assert {k: len(v) for k, v in s.fills().items()} == {k: v for k, v in live_fills.items() if v}

    rep = replay_session(s)
    cmp = compare_fills(s, rep)
    assert all(c["match"] for c in cmp.values()), cmp
    for live, again in zip(runner.accounts, rep.accounts):
        assert again.fills == live.fills
        assert again.cash == pytest.approx(live.cash)

    md = s.market_data(trading_only=True)
    assert len(md.book_ts) > 0 and md.book_ts[0] > s.calibration["ts"]
