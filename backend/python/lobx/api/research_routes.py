"""Read-only research results and recorded sessions."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from fastapi import APIRouter, HTTPException

from lobx.api.sim_service import finite
from lobx.execution.fees import FEE_PROFILES
from lobx.live.setup import RESULTS_DIR, SESSIONS_DIR

router = APIRouter()


def _safe(base: Path, name: str) -> Path:
    path = (base / name).resolve()
    if path.parent != base.resolve() or not path.exists():
        raise HTTPException(status_code=404, detail=f"{name} not found")
    return path


@router.get("/research")
def list_research() -> list[dict]:
    out = []
    for path in sorted(RESULTS_DIR.glob("*.json")):
        res = json.loads(path.read_text())
        out.append({"id": path.stem, "window": res["window"], "venue": res.get("venue"),
                    "hours": res.get("hours"), "oos_hours": res.get("oos_hours"),
                    "fees": list(res.get("fees", {})), "cross": res["window"].startswith("cross")})
    return out


@router.get("/research/{rid}")
def get_research(rid: str) -> dict:
    res = json.loads(_safe(RESULTS_DIR, f"{rid}.json").read_text())
    res["id"] = rid
    return finite(res)


@router.get("/fees")
def fee_profiles() -> dict:
    return FEE_PROFILES


@router.get("/sessions")
def list_sessions() -> list[dict]:
    from lobx.live.recorder import read_session

    out = []
    for path in sorted(SESSIONS_DIR.glob("*.jsonl.gz"), reverse=True)[:50]:
        meta = next(read_session(path), {})
        out.append({"file": path.name, "bytes": path.stat().st_size,
                    "symbol": meta.get("symbol"), "fee": meta.get("fee"),
                    "capital_usd": meta.get("capital_usd"), "started_utc": meta.get("started_utc")})
    return out


@router.get("/sessions/{name}/replay")
def replay(name: str, fee: str | None = None) -> dict:
    """Rerun a recording; with ``fee`` the same market data under another fee profile."""
    from lobx.backtest.session import compare_fills, load_session, replay_session, suite_from_meta
    from lobx.execution.simulator import ExecutionConfig

    s = load_session(_safe(SESSIONS_DIR, name))
    if s.calibration is None:
        raise HTTPException(status_code=422, detail="session ended during warm-up")
    if fee is not None:
        if fee not in FEE_PROFILES:
            raise HTTPException(status_code=422, detail=f"unknown fee profile {fee}")
        prof = FEE_PROFILES[fee]
        meta = s.meta | {"suite": s.meta["suite"] | {"maker_fee": prof["maker"]}}
        cfg = replace(ExecutionConfig(**s.calibration["config"]), maker_fee=prof["maker"],
                      taker_fee=prof["taker"])
        rep = replay_session(s, build_suite=suite_from_meta(meta), config=cfg)
    else:
        rep = replay_session(s)
    cmp = compare_fills(s, rep)
    f = rep.sim.features
    mid = 0.5 * (f.bid + f.ask)
    capital = float(s.meta.get("capital_usd", 0) or 0)
    return finite({
        "file": name, "meta": s.meta, "fee": fee or s.meta.get("fee"),
        "events": len(s.events), "by_kind": s.by_kind,
        "accounts": [{"name": a.name, "net_pnl": a.equity_at(mid), "fees": a.fees,
                      "fills": len(a.fills), "position": a.position,
                      "return_bps": a.equity_at(mid) / capital * 1e4 if capital else None,
                      "equity_curve": a.equity[:: max(1, len(a.equity) // 400)],
                      **cmp[a.name]} for a in rep.accounts],
    })
