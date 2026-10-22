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
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import numpy as np

from lobx.analytics.calibration import calibrate, fair_value_r2, fit_sigma, select_fair_value
from lobx.analytics.performance import block_bootstrap, deflated_sharpe
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
ALPHA_PAUSE_GRID = (0.0, 0.01, 0.025, 0.05, 0.1)
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
                   maker_fee: float, qty: float = QTY,
                   alpha_gates: dict[str, float] | None = None) -> list:
    """The seven strategies compared everywhere. ``qty`` is the size of one quote.

    Inventory enters every model in lots (position / qty), so gamma keeps its
    meaning when the quote size is scaled.
    """
    A, k = calib["arrival"]["A"], calib["arrival"]["k"]
    fv = calib["fair_value"]
    fair = FairValueModel(fv["b_micro"], fv["b_flow"], fv["b_obi"], fv.get("b_volume", 0.0))
    alpha_gates = alpha_gates or {"base": 0.025, "fee_floor": 0.025}
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
        GLFTStrategy(
            gamma_glft, A, k, qty, hawkes_widen=HAWKES_WIDEN,
            hawkes_pause_ratio=HAWKES_PAUSE, fair_value=fair, maker_fee=maker_fee,
            sigma_reference=calib["sigma"], sigma_power=0.5, asymmetric_hawkes=True,
            directional_pause=True, toxicity_widen=0.5, queue_widen=0.25,
            label="AdaptiveGLFT",
        ),
        GLFTStrategy(
            gamma_glft, A, k, qty, hawkes_widen=HAWKES_WIDEN,
            hawkes_pause_ratio=HAWKES_PAUSE, fair_value=fair, maker_fee=maker_fee,
            sigma_reference=calib["sigma"], sigma_power=0.5, asymmetric_hawkes=True,
            directional_pause=True, toxicity_widen=0.5, queue_widen=0.25,
            fee_floor=True, edge_margin_bps=0.2, label="AdaptiveGLFT fee-floor",
        ),
        GLFTStrategy(
            gamma_glft, A, k, qty, hawkes_widen=HAWKES_WIDEN,
            hawkes_pause_ratio=HAWKES_PAUSE, fair_value=fair, maker_fee=maker_fee,
            sigma_reference=calib["sigma"], sigma_power=0.5, asymmetric_hawkes=True,
            directional_pause=True, toxicity_widen=0.5, queue_widen=0.25,
            alpha_pause_sigma=alpha_gates["base"], label="AdaptiveGLFT alpha-gated",
        ),
        GLFTStrategy(
            gamma_glft, A, k, qty, hawkes_widen=HAWKES_WIDEN,
            hawkes_pause_ratio=HAWKES_PAUSE, fair_value=fair, maker_fee=maker_fee,
            sigma_reference=calib["sigma"], sigma_power=0.5, asymmetric_hawkes=True,
            directional_pause=True, toxicity_widen=0.5, queue_widen=0.25,
            fee_floor=True, edge_margin_bps=0.2,
            alpha_pause_sigma=alpha_gates["fee_floor"],
            label="AdaptiveGLFT alpha-gated fee-floor",
        ),
        GLFTStrategy(
            gamma_glft, A, k, qty, hawkes_widen=HAWKES_WIDEN,
            hawkes_pause_ratio=HAWKES_PAUSE, fair_value=fair, maker_fee=maker_fee,
            sigma_reference=calib["sigma"], sigma_power=0.5, asymmetric_hawkes=True,
            directional_pause=True, toxicity_widen=0.5, queue_widen=0.25,
            alpha_pause_sigma=alpha_gates["base"],
            enabled=alpha_gates.get("base_active", True),
            label="AdaptiveGLFT robust-gated",
        ),
        GLFTStrategy(
            gamma_glft, A, k, qty, hawkes_widen=HAWKES_WIDEN,
            hawkes_pause_ratio=HAWKES_PAUSE, fair_value=fair, maker_fee=maker_fee,
            sigma_reference=calib["sigma"], sigma_power=0.5, asymmetric_hawkes=True,
            directional_pause=True, toxicity_widen=0.5, queue_widen=0.25,
            fee_floor=True, edge_margin_bps=0.2,
            alpha_pause_sigma=alpha_gates["fee_floor"],
            enabled=alpha_gates.get("fee_floor_active", True),
            label="AdaptiveGLFT robust-gated fee-floor",
        ),
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


def tune_alpha_gate(data: MarketData, calib: dict, fee: str, gamma: float) -> dict:
    """Tune the adverse-side gate for worst-case base/150 ms inner-fold PnL."""
    prof = FEE_PROFILES[fee]
    A, k = calib["arrival"]["A"], calib["arrival"]["k"]
    fv = calib["fair_value"]
    fair = FairValueModel(fv["b_micro"], fv["b_flow"], fv["b_obi"], fv.get("b_volume", 0.0))
    cfg = exec_config(data, fee)
    accounts = []
    for floor in (False, True):
        for threshold in ALPHA_PAUSE_GRID:
            strategy = GLFTStrategy(
                gamma, A, k, QTY, hawkes_widen=HAWKES_WIDEN,
                hawkes_pause_ratio=HAWKES_PAUSE, fair_value=fair,
                maker_fee=prof["maker"], sigma_reference=calib["sigma"], sigma_power=0.5,
                asymmetric_hawkes=True, directional_pause=True, toxicity_widen=0.5,
                queue_widen=0.25, fee_floor=floor, edge_margin_bps=0.2,
                alpha_pause_sigma=threshold,
            )
            prefix = "floor" if floor else "base"
            accounts.append(SimAccount(strategy, cfg, name=f"{prefix} {threshold:g}"))
            latency_cfg = exec_config(data, fee, feed_latency_ms=150, order_latency_ms=150)
            accounts.append(SimAccount(deepcopy(strategy), latency_cfg,
                                       name=f"{prefix} {threshold:g} latency150"))
    run = run_backtest(data, accounts, make_features(data, calib))
    rows = run.by_name()

    def best(prefix: str) -> tuple[float, bool, float]:
        candidates = [(threshold, rows[f"{prefix} {threshold:g}"])
                      for threshold in ALPHA_PAUSE_GRID]
        active = [item for item in candidates if item[1]["fills_per_hour"] >= 25.0]
        pool = active or candidates
        selected = max(pool, key=lambda item: (
            min(item[1]["net_pnl"],
                rows[f"{prefix} {item[0]:g} latency150"]["net_pnl"]),
            item[1]["net_pnl"],
            item[1]["fill_expectancy_5000ms"],
        ))
        robust_pnl = min(selected[1]["net_pnl"],
                         rows[f"{prefix} {selected[0]:g} latency150"]["net_pnl"])
        return selected[0], robust_pnl > 0.0, robust_pnl

    base_threshold, base_active, base_robust_pnl = best("base")
    floor_threshold, floor_active, floor_robust_pnl = best("floor")

    return {
        "base": base_threshold,
        "base_active": base_active,
        "base_robust_pnl": base_robust_pnl,
        "fee_floor": floor_threshold,
        "fee_floor_active": floor_active,
        "fee_floor_robust_pnl": floor_robust_pnl,
        "grid": {
            prefix: [{"threshold_sigma": threshold,
                      "net_pnl": rows[f"{prefix} {threshold:g}"]["net_pnl"],
                      "latency150_net_pnl":
                          rows[f"{prefix} {threshold:g} latency150"]["net_pnl"],
                      "fills": rows[f"{prefix} {threshold:g}"]["fills"]}
                     for threshold in ALPHA_PAUSE_GRID]
            for prefix in ("base", "floor")
        },
        "seconds": run.seconds,
    }


def _clean(report: dict) -> dict:
    return {k: v for k, v in report.items() if not k.startswith("_")}


def evaluate_window(data: MarketData, fees: tuple[str, ...] = ("vip0", "mm_rebate"),
                    log=print, nested: bool = False) -> dict:
    """Evaluate one chronological holdout.

    With ``nested=True`` the outer training half is itself split: model
    calibration uses its first half and gamma selection uses its second half.
    The final model is then refit on all outer training data before the unseen
    outer half is evaluated.  This prevents gamma from being selected on the
    same observations that estimated sigma, A, k, Hawkes and fair value.
    """
    ins, oos = data.split(0.5)
    log(f"[{data.name}] {data.hours:.1f} h, {len(data.book_ts):,} book updates, "
        f"{len(data.trade_ts):,} trades, tick {data.tick_size}")
    if not nested:
        return evaluate(ins, oos, data.name, fees, log)
    calibration_data, tuning_data = ins.split(0.5)
    return evaluate(ins, oos, data.name, fees, log, calibration_data, tuning_data)


def cross_regime(train: MarketData, test: MarketData,
                 fees: tuple[str, ...] = ("vip0", "mm_rebate"), log=print) -> dict:
    """Calibrate and tune on one regime, evaluate unchanged on another."""
    name = f"cross {train.name} -> {test.name}"
    log(f"[{name}]")
    return evaluate(train, test, name, fees, log)


def evaluate(ins: MarketData, oos: MarketData, name: str,
             fees: tuple[str, ...] = ("vip0", "mm_rebate"), log=print,
             calibration_data: MarketData | None = None,
             tuning_data: MarketData | None = None) -> dict:
    t_start = time.time()
    data = oos
    selection_data = calibration_data or ins
    tuning_data = tuning_data or ins
    selection_calib = calibrate(selection_data)
    selected_fair = select_fair_value(selection_data, tuning_data, selection_calib)
    selection_calib["fair_value"] = selected_fair
    # Refit on every observation available before OOS after hyperparameters
    # have been selected.  This is standard nested time-series validation.
    nested = calibration_data is not None or tuning_data is not ins
    calib = (calibrate(ins, selected_fair["horizon_ms"], selected_fair["enabled"])
             if nested else selection_calib)
    calib["fair_value"]["selection_validation_r2"] = selected_fair["validation_r2"]
    calib["fair_value"]["candidate_r2"] = selected_fair["candidate_r2"]
    calib["fair_value"]["r2_out_of_sample"] = fair_value_r2(oos, calib)
    log(f"  calibration: sigma={calib['sigma']:.3f} USD/sqrt(s)  A={calib['arrival']['A']:.2f}/s"
        f"  k={calib['arrival']['k']:.2f}/USD (R2 {calib['arrival']['r2']:.2f})  Hawkes n="
        f"{calib['hawkes']['buy']['branching_ratio']:.2f}/{calib['hawkes']['sell']['branching_ratio']:.2f}"
        f"  FV {'on' if calib['fair_value']['enabled'] else 'off'}"
        f" @{calib['fair_value']['horizon_ms']}ms R2 tune/IS/OOS "
        f"{selected_fair['validation_r2']:.3f}/{calib['fair_value']['r2_in_sample']:.3f}/"
        f"{calib['fair_value']['r2_out_of_sample']:.3f}")

    result: dict = {"window": name, "venue": oos.venue, "hours": ins.hours + oos.hours,
                    "in_sample": [ins.start, ins.end], "out_of_sample": [oos.start, oos.end],
                    "oos_hours": oos.hours, "oos_sigma": float(fit_sigma(oos)),
                    "validation": {
                        "method": "nested chronological" if nested else "single chronological",
                        "calibration": [selection_data.start, selection_data.end],
                        "tuning": [tuning_data.start, tuning_data.end],
                        "final_refit": [ins.start, ins.end],
                    },
                    "calibration": calib, "fees": {}}
    for fee in fees:
        tuned = tune_gamma(tuning_data, selection_calib, fee)
        tuned["alpha_gate"] = tune_alpha_gate(
            tuning_data, selection_calib, fee, tuned["GLFT"])
        log(f"  [{fee}] tuned gamma: AS={tuned['AS']:g}  GLFT={tuned['GLFT']:g} "
            f"alpha gate={tuned['alpha_gate']['base']:g}/"
            f"{tuned['alpha_gate']['fee_floor']:g} sigma "
            f"active={tuned['alpha_gate']['base_active']}/"
            f"{tuned['alpha_gate']['fee_floor_active']} ({tuned['seconds']:.0f}s + "
            f"{tuned['alpha_gate']['seconds']:.0f}s)")
        prof = FEE_PROFILES[fee]
        cfg = exec_config(oos, fee)
        suite = strategy_suite(calib, data.tick_size, tuned["AS"], tuned["GLFT"], prof["maker"],
                               alpha_gates=tuned["alpha_gate"])
        accounts = [SimAccount(s, cfg) for s in suite]
        # Stress variants of the full model, run in the same pass on the same data.
        hero = lambda: strategy_suite(
            calib, data.tick_size, tuned["AS"], tuned["GLFT"], prof["maker"],
            alpha_gates=tuned["alpha_gate"])[11]
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
        # Eleven gamma values for each of two model families were inspected.
        # DSR is diagnostic: OOS remains the actual promotion gate.
        for r in main:
            r["deflated_sharpe_probability"] = deflated_sharpe(
                r["_minute_pnl"], len(GAMMA_GRID) * 2 + len(ALPHA_PAUSE_GRID) * 2)
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


def walk_forward_slices(data: MarketData, train_s: float, test_s: float,
                        step_s: float | None = None) -> list[tuple[MarketData, MarketData]]:
    """Rolling, past-only train/test slices; never uses an expanding future."""
    if min(train_s, test_s) <= 0:
        raise ValueError("train_s and test_s must be positive")
    step_ms = int((step_s or test_s) * 1000)
    train_ms, test_ms = int(train_s * 1000), int(test_s * 1000)
    if step_ms <= 0:
        raise ValueError("step_s must be positive")
    folds = []
    test_start = data.start + train_ms
    i = 0
    # Recorded windows conventionally end just before their exclusive wall
    # clock boundary (e.g. the final 100 ms book update is at 59.900 s).
    while test_start + test_ms <= data.end + 1_000:
        train = data.slice(test_start - train_ms, test_start,
                           f"{data.name}:wf{i}:train")
        test = data.slice(test_start, test_start + test_ms,
                          f"{data.name}:wf{i}:test")
        if len(train.book_ts) and len(test.book_ts):
            folds.append((train, test))
        test_start += step_ms
        i += 1
    return folds


def walk_forward(data: MarketData, train_s: float, test_s: float,
                 fees: tuple[str, ...] = ("vip0",), step_s: float | None = None,
                 log=print) -> dict:
    """Nested rolling evaluation.  Every fold is an independent OOS result."""
    folds = walk_forward_slices(data, train_s, test_s, step_s)
    if not folds:
        raise ValueError("data is shorter than train_s + test_s")
    results = []
    for i, (train, test) in enumerate(folds):
        calibration_data, tuning_data = train.split(0.5)
        results.append(evaluate(train, test, f"{data.name}:walk-forward {i}", fees, log,
                                calibration_data, tuning_data))
    return {
        "window": f"walk-forward {data.name}",
        "venue": data.venue,
        "validation": {"method": "nested rolling", "train_s": train_s, "test_s": test_s,
                       "step_s": step_s or test_s, "folds": len(results)},
        "folds": results,
    }


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
