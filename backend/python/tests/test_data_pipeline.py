"""Historical range construction and manifest integrity checks."""
from __future__ import annotations

import hashlib
import json

import pytest

from lobx.backtest import data as data_module
from lobx.market_data.collector import Collector
from lobx.market_data.depth_stream_manager import DepthStreamManager
from scripts.fetch_history import _daily_ranges


def test_daily_ranges_cover_cross_midnight_without_overlap():
    rows = _daily_ranges("2024-03-01", "23:30", "2024-03-03", "00:30")
    assert [r[0] for r in rows] == ["2024-03-01", "2024-03-02", "2024-03-03"]
    assert rows[0][2] == rows[1][1]
    assert rows[1][2] == rows[2][1]
    assert sum(hi - lo for _day, lo, hi in rows) == 25 * 3_600_000


def test_daily_ranges_reject_reverse_interval():
    with pytest.raises(ValueError):
        _daily_ranges("2024-03-02", "10:00", "2024-03-02", "09:00")


def test_verify_history_detects_corruption(tmp_path, monkeypatch):
    book, trades = tmp_path / "book.parquet", tmp_path / "trades.parquet"
    book.write_bytes(b"book")
    trades.write_bytes(b"trades")
    digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    manifest = {"windows": [{
        "label": "x", "book_file": book.name, "trades_file": trades.name,
        "book_sha256": digest(book), "trades_sha256": digest(trades),
    }]}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(data_module, "HISTORY_DIR", tmp_path)
    assert data_module.verify_history("x")["ok"]
    trades.write_bytes(b"changed")
    check = data_module.verify_history("x")
    assert not check["ok"]
    assert check["book"]["ok"] and not check["trades"]["ok"]


def test_futures_depth_uses_previous_update_id_chain():
    mgr = DepthStreamManager("BTCUSDT", market="futures")
    mgr._state.synced = True
    mgr._state.prev_final_u = 100
    mgr._apply_event({"U": 95, "u": 110, "pu": 100, "b": [["100", "2"]], "a": []})
    assert mgr.book.best_bid() == (100.0, 2.0)
    assert mgr._state.prev_final_u == 110


def test_market_selects_futures_public_endpoints():
    collector = Collector("BTCUSDT", market="futures", enable_engine_mirror=False)
    assert collector.depth_mgr.market == "futures"
    assert collector._ws._url.startswith("wss://fstream.binance.com/stream")
    with pytest.raises(ValueError):
        Collector("BTCUSDT", market="wrong", enable_engine_mirror=False)
