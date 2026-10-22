"""Assemble a live paper session (shared by the terminal and the web API)."""
from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path

from lobx.backtest import research
from lobx.backtest.data import ROOT
from lobx.execution.fees import FEE_PROFILES
from lobx.execution.simulator import ExecutionConfig
from lobx.live.quote_loop import LiveSimRunner
from lobx.live.recorder import SessionRecorder

RESULTS_DIR = ROOT / "results" / "research"
SESSIONS_DIR = ROOT / "data" / "sessions"
DEFAULT_GAMMA = 0.01
DECISION_MS = 100
EVENT_HISTORY = 20_000


def get_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "lobx"})
    with urllib.request.urlopen(req, timeout=8.0) as resp:
        return json.loads(resp.read().decode())


def spot_filters(symbol: str) -> tuple[float, float]:
    """(tick size, lot step) of a Binance spot symbol."""
    info = get_json(f"https://api.binance.com/api/v3/exchangeInfo?symbol={symbol}")
    f = {x["filterType"]: x for x in info["symbols"][0]["filters"]}
    return float(f["PRICE_FILTER"]["tickSize"]), float(f["LOT_SIZE"]["stepSize"])


def latest_calibration(fee: str = "vip0", results_dir: Path = RESULTS_DIR) -> dict | None:
    """Calibration plus tuned gammas from a single-window research result.

    Calm windows are preferred (the normal state of the market), newest first.
    """
    files = sorted(results_dir.glob("*.json"),
                   key=lambda p: ("calm" not in p.name, -p.stat().st_mtime))
    for path in files:
        res = json.loads(path.read_text())
        if "calibration" not in res or res["window"].startswith("cross"):
            continue
        tuning = res["fees"].get(fee, next(iter(res["fees"].values()), {})).get("tuning", {})
        return res["calibration"] | {
            "_source": path.name,
            "_gamma_as": tuning.get("AS", DEFAULT_GAMMA),
            "_gamma_glft": tuning.get("GLFT", DEFAULT_GAMMA),
        }
    return None


def build_live_sim(symbol: str = "BTCUSDT", fee: str = "spot_vip0", warmup_s: float = 120.0,
                   capital_usd: float = 10_000.0, record: bool = True,
                   sessions_dir: Path = SESSIONS_DIR) -> LiveSimRunner:
    from lobx.market_data.collector import Collector

    prof = FEE_PROFILES[fee]
    tick, lot_step = spot_filters(symbol)
    cfg = ExecutionConfig(tick_size=tick, maker_fee=prof["maker"], taker_fee=prof["taker"])
    tuned = latest_calibration() or {}
    suite = {"gamma_as": tuned.get("_gamma_as", DEFAULT_GAMMA),
             "gamma_glft": tuned.get("_gamma_glft", DEFAULT_GAMMA), "maker_fee": prof["maker"]}

    def build(calib: dict, qty: float):
        return research.strategy_suite(calib, tick, qty=qty, **suite)

    recorder = None
    if record:
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        recorder = SessionRecorder(sessions_dir / f"{symbol}_{stamp}.jsonl.gz", {
            "symbol": symbol, "venue": "Binance spot", "fee": fee, "fee_label": prof["label"],
            "tick_size": tick, "lot_step": lot_step, "capital_usd": capital_usd,
            "warmup_s": warmup_s, "decision_ms": DECISION_MS, "event_history": EVENT_HISTORY,
            "suite": suite, "gamma_source": tuned.get("_source"),
            "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    collector = Collector(symbol=symbol, enable_engine_mirror=False, enable_storage=False,
                          heartbeat_s=60)
    return LiveSimRunner(collector, build, cfg, warmup_s=warmup_s, decision_ms=DECISION_MS,
                         capital_usd=capital_usd, lot_step=lot_step, recorder=recorder)
