"""Research protocol: calibrate -> tune gamma in-sample -> evaluate out-of-sample.

For each data window:
  1. split in time: first half in-sample (IS), second half out-of-sample (OOS)
  2. calibrate sigma, A, k, Hawkes and the fair-value model on IS only
  3. choose gamma for AS and GLFT by IS net PnL among settings with at least
     MIN_FILLS_PER_HOUR fills, separately per fee profile
  4. run every strategy once on OOS with frozen parameters
  5. moving-block bootstrap of OOS per-minute PnL (paired vs FixedSpread)
  6. stress the best model on OOS: latency, optimistic queue, fee tier
Nothing estimated on OOS feeds back into parameters.
"""
from __future__ import annotations

import json
import time
from dataclasses import replace
from pathlib import Path

import numpy as np

from lobx.analytics.calibration import calibrate, fair_value_r2, fit_sigma
from lobx.analytics.performance import block_bootstrap
from lobx.backtest.data import MarketData
from lobx.backtest.engine import run_backtest
from lobx.execution.fees import FEE_PROFILES
from lobx.execution.simulator import ExecutionConfig, SimAccount
from lobx.strategy import (
    AvellanedaStoikovStrategy,
    FairValueModel,
    FixedSpreadStrategy,
    GLFTStrategy,
    InventorySkewStrategy,
    NoTradeStrategy,
)
from lobx.strategy.features import FeatureEngine, HawkesParams

GAMMA_GRID = (1e-4, 3e-4, 1e-3, 3e-3, 1e-2, 3e-2, 0.1, 0.3, 1.0, 3.0, 10.0)
QTY = 0.001
MAX_POSITION = 0.01
AS_HORIZON_S = 60.0
HAWKES_WIDEN = 0.5
HAWKES_PAUSE = 3.0
# Gamma is chosen by in-sample net PnL among settings that actually make a
# market. Without this floor, when every setting loses, the "best" gamma is
# simply the one that quotes so wide it almost never trades.
MIN_FILLS_PER_HOUR = 100.0


def make_features(data: MarketData, calib: dict) -> FeatureEngine:
    return FeatureEngine(
        data.tick_size,
        HawkesParams.from_dict(calib["hawkes"]["buy"]),
        HawkesParams.from_dict(calib["hawkes"]["sell"]),
        sigma_seed=calib["sigma"],
        calibrated=True,
    )


def exec_config(data: MarketData, fee: str, **overrides) -> ExecutionConfig:
    prof = FEE_PROFILES[fee]
    base = ExecutionConfig(tick_size=data.tick_size, maker_fee=prof["maker"],
                           taker_fee=prof["taker"], max_position=MAX_POSITION,
                           max_drawdown=None)
    return replace(base, **overrides)


def strategy_suite(calib: dict, tick: float, gamma_as: float, gamma_glft: float,
                   maker_fee: float, qty: float = QTY) -> list:
    """The seven strategies compared everywhere. ``qty`` is the size of one quote.

    Inventory enters every model in lots (position / qty), so gamma keeps its
    meaning when the quote size is scaled.
    """
    A, k = calib["arrival"]["A"], calib["arrival"]["k"]
    fv = calib["fair_value"]
    fair = FairValueModel(fv["b_micro"], fv["b_flow"], fv["b_obi"])
    half_tick = tick / 2.0
    return [
        NoTradeStrategy(qty, label="NoTrade"),
        FixedSpreadStrategy(half_tick, qty, label="FixedSpread"),
        InventorySkewStrategy(half_tick, qty, skew_per_lot=tick / 2.0, label="InventorySkew"),
        AvellanedaStoikovStrategy(gamma_as, k, AS_HORIZON_S, qty, label="AS"),
        GLFTStrategy(gamma_glft, A, k, qty, label="GLFT"),
        GLFTStrategy(gamma_glft, A, k, qty, hawkes_widen=HAWKES_WIDEN,
                     hawkes_pause_ratio=HAWKES_PAUSE, fair_value=fair, label="GLFT+Hawkes+FV"),
        GLFTStrategy(gamma_glft, A, k, qty, hawkes_widen=HAWKES_WIDEN,
                     hawkes_pause_ratio=HAWKES_PAUSE, fair_value=fair, maker_fee=maker_fee,
                     fee_aware=True, label="GLFT+HFV fee-aware"),
    ]


def size_from_capital(capital_usd: float, mid: float, lot_step: float = 1e-5,
                      quote_fraction: float = 0.1) -> tuple[float, float]:
    """(quote size, position limit) in base units for an unlevered account.

    One quote is ``quote_fraction`` of capital, and the position limit is the
    whole capital, i.e. at most 1 / quote_fraction lots of inventory.
    """
    if capital_usd <= 0 or mid <= 0:
        raise ValueError("capital and mid must be positive")
    qty = max(lot_step, round(capital_usd * quote_fraction / mid / lot_step) * lot_step)
    return round(qty, 10), round(capital_usd / mid, 10)


def tune_gamma(data: MarketData, calib: dict, fee: str) -> dict:
    A, k = calib["arrival"]["A"], calib["arrival"]["k"]
    cfg = exec_config(data, fee)
    accounts = []
    for g in GAMMA_GRID:
        accounts.append(SimAccount(AvellanedaStoikovStrategy(g, k, AS_HORIZON_S, QTY), cfg,
                                   name=f"AS g={g:g}"))
        accounts.append(SimAccount(GLFTStrategy(g, A, k, QTY), cfg, name=f"GLFT g={g:g}"))
    run = run_backtest(data, accounts, make_features(data, calib))
    rows = run.by_name()

    def best(prefix: str) -> float:
        cands = [rows[f"{prefix} g={g:g}"] | {"gamma": g} for g in GAMMA_GRID]
        active = [r for r in cands if r["fills_per_hour"] >= MIN_FILLS_PER_HOUR]
        if not active:
            return max(cands, key=lambda r: r["fills"])["gamma"]
        return max(active, key=lambda r: (r["net_pnl"], r["sharpe"]))["gamma"]

    table = [{"gamma": g, **{p: {m: rows[f"{p} g={g:g}"][m] for m in
                                 ("net_pnl", "sharpe", "fills", "fills_per_hour", "max_drawdown")}
                             for p in ("AS", "GLFT")}} for g in GAMMA_GRID]
    return {"AS": best("AS"), "GLFT": best("GLFT"), "grid": table, "seconds": run.seconds}


def _clean(report: dict) -> dict:
    return {k: v for k, v in report.items() if not k.startswith("_")}


def evaluate_window(data: MarketData, fees: tuple[str, ...] = ("vip0", "mm_rebate"),
                    log=print) -> dict:
    """Calibrate and tune on the first half, evaluate on the second half."""
    ins, oos = data.split(0.5)
    log(f"[{data.name}] {data.hours:.1f} h, {len(data.book_ts):,} book updates, "
        f"{len(data.trade_ts):,} trades, tick {data.tick_size}")
    return evaluate(ins, oos, data.name, fees, log)


def cross_regime(train: MarketData, test: MarketData,
                 fees: tuple[str, ...] = ("vip0", "mm_rebate"), log=print) -> dict:
    """Calibrate and tune on one regime, evaluate unchanged on another."""
    name = f"cross {train.name} -> {test.name}"
    log(f"[{name}]")
    return evaluate(train, test, name, fees, log)


def evaluate(ins: MarketData, oos: MarketData, name: str,
             fees: tuple[str, ...] = ("vip0", "mm_rebate"), log=print) -> dict:
    t_start = time.time()
    data = oos
    calib = calibrate(ins)
    calib["fair_value"]["r2_out_of_sample"] = fair_value_r2(oos, calib)
    log(f"  calibration: sigma={calib['sigma']:.3f} USD/sqrt(s)  A={calib['arrival']['A']:.2f}/s"
        f"  k={calib['arrival']['k']:.2f}/USD (R2 {calib['arrival']['r2']:.2f})  Hawkes n="
        f"{calib['hawkes']['buy']['branching_ratio']:.2f}/{calib['hawkes']['sell']['branching_ratio']:.2f}"
        f"  FV R2 IS/OOS {calib['fair_value']['r2_in_sample']:.3f}/"
        f"{calib['fair_value']['r2_out_of_sample']:.3f}")

    result: dict = {"window": name, "venue": oos.venue, "hours": ins.hours + oos.hours,
                    "in_sample": [ins.start, ins.end], "out_of_sample": [oos.start, oos.end],
                    "oos_hours": oos.hours, "oos_sigma": float(fit_sigma(oos)),
                    "calibration": calib, "fees": {}}
    for fee in fees:
        tuned = tune_gamma(ins, calib, fee)
        log(f"  [{fee}] tuned gamma: AS={tuned['AS']:g}  GLFT={tuned['GLFT']:g} "
            f"({tuned['seconds']:.0f}s)")
        prof = FEE_PROFILES[fee]
        cfg = exec_config(oos, fee)
        suite = strategy_suite(calib, data.tick_size, tuned["AS"], tuned["GLFT"], prof["maker"])
        accounts = [SimAccount(s, cfg) for s in suite]
        # Stress variants of the full model, run in the same pass on the same data.
        hero = lambda: strategy_suite(calib, data.tick_size, tuned["AS"], tuned["GLFT"],
                                      prof["maker"])[5]
        stress = {
            "latency 50+50ms": exec_config(oos, fee, feed_latency_ms=50, order_latency_ms=50),
            "latency 150+150ms": exec_config(oos, fee, feed_latency_ms=150, order_latency_ms=150),
            "optimistic queue": exec_config(oos, fee, queue_model="optimistic"),
            "requote 500ms": exec_config(oos, fee, min_requote_ms=500),
        }
        for label, scfg in stress.items():
            accounts.append(SimAccount(hero(), scfg, name=f"stress: {label}"))
        fx_opt = SimAccount(FixedSpreadStrategy(data.tick_size / 2, QTY), exec_config(
            oos, fee, queue_model="optimistic"), name="stress: FixedSpread optimistic queue")
        accounts.append(fx_opt)
        run = run_backtest(oos, accounts, make_features(oos, calib))
        reports = run.reports
        main = [r for r in reports if not r["strategy"].startswith("stress")]
        boot = block_bootstrap({r["strategy"]: r["_minute_pnl"] for r in main},
                               baseline="FixedSpread")
        log(f"  [{fee}] OOS {oos.hours:.1f} h in {run.seconds:.0f}s")
        for r in main:
            log(f"    {r['strategy']:<20} net {r['net_pnl']:+9.3f}  fills {r['fills']:6d}  "
                f"sharpe {r['sharpe']:+7.2f}  adverse {r['adverse_selection_cost']:+8.3f}  "
                f"maxDD {r['max_drawdown']:7.3f}")
        result["fees"][fee] = {
            "profile": prof,
            "tuning": tuned,
            "oos": [_clean(r) for r in main],
            "stress": [_clean(r) for r in reports if r["strategy"].startswith("stress")],
            "bootstrap": boot,
            "oos_seconds": run.seconds,
        }
    result["seconds"] = time.time() - t_start
    return result


def save_result(result: dict, out_dir: str | Path) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    slug = "".join(c if c.isalnum() else "_" for c in result["window"]).strip("_")
    path = out / f"{slug}.json"
    path.write_text(json.dumps(result, indent=2, default=_json_default))
    return path


def _json_default(o):
    if isinstance(o, (np.floating, np.integer)):
        return o.item()
    if isinstance(o, np.ndarray):
        return o.tolist()
    raise TypeError(type(o))
