"""REST endpoints used by the React frontend (no network: the live sim is not started)."""
import json

import pytest
from fastapi.testclient import TestClient

from lobx.api import research_routes
from lobx.api.sim_service import finite
from main import app

client = TestClient(app)


def test_health_and_idle_sim():
    h = client.get("/api/v1/health").json()
    assert h["status"] == "ok" and h["sim"] == "idle"
    s = client.get("/api/v1/sim/state").json()
    assert s["status"] == "idle" and "spot_vip0" in s["fees"]


def test_research_listing_and_detail(tmp_path, monkeypatch):
    res = {"window": "calm test", "venue": "x", "hours": 1.0, "oos_hours": 0.5,
           "calibration": {"sigma": 1.0}, "fees": {"vip0": {"oos": [{"sharpe": float("nan")}]}}}
    (tmp_path / "calm_test.json").write_text(json.dumps(res))
    monkeypatch.setattr(research_routes, "RESULTS_DIR", tmp_path)
    rows = client.get("/api/v1/research").json()
    assert rows == [{"id": "calm_test", "window": "calm test", "venue": "x", "hours": 1.0,
                     "oos_hours": 0.5, "fees": ["vip0"], "cross": False}]
    detail = client.get("/api/v1/research/calm_test").json()
    assert detail["fees"]["vip0"]["oos"][0]["sharpe"] is None
    assert client.get("/api/v1/research/..%2Fsecret").status_code == 404
    assert client.get("/api/v1/research/missing").status_code == 404


def test_sim_start_validates_input():
    assert client.post("/api/v1/sim/start", json={"fee": "nope"}).status_code == 422
    assert client.post("/api/v1/sim/start", json={"symbol": "../x"}).status_code == 422
    assert client.post("/api/v1/sim/start", json={"capital_usd": -1}).status_code == 422


def test_replay_endpoint(tmp_path, monkeypatch):
    from tests.test_session_replay import test_live_session_records_and_replays_exactly

    test_live_session_records_and_replays_exactly(tmp_path)
    monkeypatch.setattr(research_routes, "SESSIONS_DIR", tmp_path)
    listing = client.get("/api/v1/sessions").json()
    assert listing[0]["file"] == "TEST.jsonl.gz" and listing[0]["capital_usd"] == 5_000
    out = client.get("/api/v1/sessions/TEST.jsonl.gz/replay").json()
    assert all(a["match"] for a in out["accounts"])
    assert any(a["fills"] > 0 for a in out["accounts"])
    rebate = client.get("/api/v1/sessions/TEST.jsonl.gz/replay?fee=mm_rebate").json()
    assert rebate["fee"] == "mm_rebate"
    assert client.get("/api/v1/sessions/TEST.jsonl.gz/replay?fee=bad").status_code == 422


def test_finite():
    assert finite({"a": [1.0, float("inf"), (float("nan"),)], "b": "x"}) == \
        {"a": [1.0, None, [None]], "b": "x"}
    assert finite(3) == 3
    with pytest.raises(ValueError):
        json.dumps(float("nan"), allow_nan=False)
