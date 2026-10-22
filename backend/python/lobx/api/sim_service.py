"""One live paper session for the web UI: start/stop over REST, state over a websocket.

The session is the same ``LiveSimRunner`` the terminal uses (Binance feed ->
features -> simulator -> one account per strategy) and is recorded to
data/sessions like a terminal session.
"""
from __future__ import annotations

import asyncio
import logging
import math
import time

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from lobx.execution.fees import FEE_PROFILES
from lobx.execution.simulator import BID
from lobx.live.quote_loop import LiveSimRunner
from lobx.models.hawkes.intensity import regime
from lobx.terminal.sim_dashboard import group_fills

logger = logging.getLogger(__name__)
router = APIRouter()


class StartRequest(BaseModel):
    symbol: str = Field("BTCUSDT", pattern="^[A-Z0-9]{5,20}$")
    fee: str = "spot_vip0"
    capital_usd: float = Field(10_000.0, gt=0, le=1e9)
    warmup_s: float = Field(120.0, ge=20, le=3600)
    record: bool = True


def _quote(order, touch: float, tick: float, now: int) -> dict | None:
    if order is None:
        return None
    behind = (touch - order.price) / tick if order.side == BID else (order.price - touch) / tick
    return {"px": order.price, "qty": order.remaining, "ticks_behind": round(behind, 2),
            "sending": order.arrive_at > now, "queue_ahead": order.front}


def _thin(values: list[float], n: int = 300) -> list[float]:
    return values[:: max(1, len(values) // n)][-n:]


def finite(obj):
    """Replace NaN/inf with None: browsers reject them in JSON."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: finite(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [finite(v) for v in obj]
    return obj


class SimService:
    def __init__(self) -> None:
        self.runner: LiveSimRunner | None = None
        self.request: StartRequest | None = None
        self._tasks: list[asyncio.Task] = []
        self._reports: dict[str, dict] = {}
        self._next_report = 0.0
        self.started_at: float | None = None
        self.error: str | None = None

    @property
    def running(self) -> bool:
        return self.runner is not None and any(not t.done() for t in self._tasks)

    async def start(self, req: StartRequest) -> None:
        from lobx.live.setup import build_live_sim

        if self.running:
            raise HTTPException(status_code=409, detail="a session is already running")
        if req.fee not in FEE_PROFILES:
            raise HTTPException(status_code=422, detail=f"unknown fee profile {req.fee}")
        await self.stop()
        self.runner = await asyncio.to_thread(build_live_sim, req.symbol, req.fee, req.warmup_s,
                                              req.capital_usd, req.record)
        self.request, self.error, self._reports = req, None, {}
        self.started_at = time.time()
        self._tasks = [asyncio.create_task(self.runner.collector.run()),
                       asyncio.create_task(self._maintain())]

    async def _maintain(self) -> None:
        r = self.runner
        while r is not None and not self._tasks[0].done():
            await asyncio.sleep(0.25)
            try:
                await r.maintain()
                if time.monotonic() >= self._next_report:
                    self._reports = {x["strategy"]: x for x in r.reports()}
                    self._next_report = time.monotonic() + 5.0
            except Exception as exc:  # noqa: BLE001 - shown in the UI
                self.error = str(exc)
                logger.exception("sim maintain failed")
        if self._tasks and self._tasks[0].done() and not self._tasks[0].cancelled():
            exc = self._tasks[0].exception()
            if exc is not None:
                self.error = f"feed stopped: {exc}"

    async def stop(self) -> None:
        r = self.runner
        if r is None:
            return
        r.collector.stop()
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []
        try:
            await r.collector.close_async()
        finally:
            r.close()

    def state(self) -> dict:
        r = self.runner
        if r is None:
            return {"status": "idle", "fees": list(FEE_PROFILES)}
        f = r.features
        now = f.last_ts
        market = None
        if f.ready:
            mid = 0.5 * (f.bid + f.ask)
            lb, ls, ratio = f.intensities(now)
            market = {"ts": now, "bid": f.bid, "ask": f.ask, "mid": mid,
                      "spread": f.ask - f.bid, "spread_bps": (f.ask - f.bid) / mid * 1e4,
                      "sigma": f.vol.sigma, "lambda_buy": lb, "lambda_sell": ls,
                      "ratio": ratio, "regime": regime(ratio), "hawkes_fitted": f.calibrated}
        accounts = []
        for x in r.mark_to_market():
            rep = self._reports.get(x["name"], {})
            accounts.append({
                **{k: x[k] for k in ("name", "equity", "return_bps", "position", "fills",
                                     "filled_qty", "volume_usd", "fees", "orders", "halted")},
                "bid": _quote(x["bid"], f.bid, r.config.tick_size, now),
                "ask": _quote(x["ask"], f.ask, r.config.tick_size, now),
                "equity_curve": _thin(x["equity_curve"]),
                "edge_per_fill": rep.get("edge_per_fill_usd"),
                "adverse_bps": rep.get("adverse_bps"),
                "sharpe": rep.get("sharpe"), "max_drawdown": rep.get("max_drawdown"),
            })
        c = r.calibration
        prof = FEE_PROFILES[self.request.fee]
        return finite({
            "status": r.status if self.running else "stopped",
            "error": self.error or r.error,
            "symbol": self.request.symbol, "fee": self.request.fee, "fee_label": prof["label"],
            "maker_fee_bps": prof["maker"] * 1e4, "taker_fee_bps": prof["taker"] * 1e4,
            "capital_usd": r.capital_usd, "quote_qty": r.quote_qty,
            "max_position": r.config.max_position if r.quote_qty else None,
            "tick_size": r.config.tick_size,
            "latency_ms": [r.config.feed_latency_ms, r.config.order_latency_ms],
            "started_at": self.started_at,
            "warmup": {"elapsed_s": r.warmup_elapsed_s, "target_s": r.warmup_s,
                       "book": len(r.recorded_book), "trades": len(r.recorded_trades)},
            "recording": None if r.recorder is None else
            {"file": r.recorder.path.name, "events": r.recorder.events},
            "market": market,
            "mid_history": [[t, m] for t, m in zip(r._mid_ts[-600:], r._mids[-600:])],
            "calibration": None if c is None else {
                "sigma": c["sigma"], "A": c["arrival"]["A"], "k": c["arrival"]["k"],
                "fair_value_r2": c["fair_value"]["r2_in_sample"]},
            "accounts": accounts,
            "fills": group_fills(r.recent_fills)[-60:][::-1],
        })


service = SimService()


@router.post("/sim/start")
async def start(req: StartRequest) -> dict:
    await service.start(req)
    return service.state()


@router.post("/sim/stop")
async def stop() -> dict:
    await service.stop()
    return service.state()


@router.get("/sim/state")
def state() -> dict:
    return service.state()


async def sim_stream(websocket: WebSocket, hz: float = 2.0) -> None:
    await websocket.accept()
    try:
        while True:
            await websocket.send_json(service.state())
            await asyncio.sleep(1.0 / hz)
    except (WebSocketDisconnect, RuntimeError):
        pass
