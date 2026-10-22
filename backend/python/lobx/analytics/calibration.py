"""Model calibration from market data (in-sample windows only).

* sigma         realized mid volatility, USD/sqrt(s), 1 s previous-tick sampling
* A, k          lambda(delta) = A exp(-k delta): rate (per side, per second) of
                market orders whose execution price reaches ``delta`` USD
                beyond the mid that was visible before the order. This is the
                standard proxy for the fill intensity of a quote at depth delta
                (it ignores queue position, which the simulator then applies).
* Hawkes        exponential Hawkes MLE fitted separately to buy and sell
                market-order arrival times
* fair value    least-squares fit of the mid change over ``horizon_ms`` on
                L1 imbalance * sigma and Hawkes flow imbalance * sigma. The
                microprice deviation is not used: with a one-tick spread it
                equals imbalance * tick / 2, i.e. it is collinear with L1
                imbalance.
gamma is not estimated: it is a risk preference, chosen by an in-sample
search in ``lobx.backtest.research``.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

from lobx.analytics.volatility import realized_sigma
from lobx.backtest.data import MarketData
from lobx.models.hawkes.calibration import calibrate as hawkes_mle
from lobx.strategy.features import FeatureEngine, HawkesParams


def market_orders(data: MarketData) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Collapse trades to one row per aggressive order: (ts, worst price, is_sell)."""
    ts, px, ibm = data.trade_ts, data.trade_px, data.trade_ibm
    if len(ts) == 0:
        return ts, px, ibm
    key_change = np.flatnonzero((np.diff(ts) != 0) | (np.diff(ibm) != 0)) + 1
    starts = np.concatenate([[0], key_change])
    ends = np.concatenate([key_change, [len(ts)]])
    out_px = np.empty(len(starts))
    for n, (s, e) in enumerate(zip(starts, ends)):
        out_px[n] = px[s:e].min() if ibm[s] else px[s:e].max()
    return ts[starts], out_px, ibm[starts]


def fit_sigma(data: MarketData) -> float:
    return realized_sigma(data.book_ts, data.mids, sample_s=1.0)


def fit_arrival(data: MarketData, max_ticks: int = 30, min_count: int = 20) -> dict:
    ts, px, is_sell = market_orders(data)
    idx = np.searchsorted(data.book_ts, ts, side="left") - 1
    ok = idx >= 0
    ts, px, is_sell, idx = ts[ok], px[ok], is_sell[ok], idx[ok]
    mid = data.mids[idx]
    depth = np.where(is_sell, mid - px, px - mid)
    T = max((data.end - data.start) / 1000.0, 1e-9)
    tick = data.tick_size
    grid = tick / 2.0 + tick * np.arange(max_ticks)
    sorted_depth = np.sort(depth)
    counts = len(sorted_depth) - np.searchsorted(sorted_depth, grid - 1e-9, side="left")
    rates = counts / (2.0 * T)
    use = counts >= min_count
    if use.sum() < 3:
        raise ValueError("not enough market orders beyond the touch to fit A and k")
    x, y = grid[use], np.log(rates[use])
    slope, intercept = np.polyfit(x, y, 1)
    pred = intercept + slope * x
    r2 = 1.0 - np.sum((y - pred) ** 2) / max(np.sum((y - y.mean()) ** 2), 1e-12)
    return {
        "A": float(math.exp(intercept)),
        "k": float(-slope),
        "r2": float(r2),
        "depths": [float(v) for v in x],
        "rates": [float(v) for v in rates[use]],
        "market_orders": int(len(depth)),
    }


def fit_hawkes(data: MarketData, max_events: int = 20_000) -> dict:
    ts, _, is_sell = market_orders(data)
    out = {}
    for side, mask in (("buy", ~is_sell), ("sell", is_sell)):
        times = ts[mask][:max_events] / 1000.0
        T = (times[-1] - times[0]) if len(times) > 1 else 1.0
        out[side] = hawkes_mle(times, T=T + 1e-6, n_starts=3)
    return out


def _feature_samples(data: MarketData, hawkes: dict, sigma: float, horizon_ms: int,
                     sample_ms: int = 100) -> tuple[np.ndarray, np.ndarray]:
    """Past-only features and the realised future mid change.

    Columns are Hawkes flow, L1 imbalance and signed recent trade volume,
    each scaled by current sigma so coefficients remain dimensionless.
    """
    fe = FeatureEngine(data.tick_size, HawkesParams.from_dict(hawkes["buy"]),
                       HawkesParams.from_dict(hawkes["sell"]), sigma_seed=sigma, calibrated=True)
    rows_t, rows_x = [], []
    next_t = 0
    for ev in data.events():
        if ev[0] == "T":
            fe.on_trade(ev[1], ev[2], ev[3], ev[4])
            continue
        _, ts, b, bq, a, aq = ev
        fe.on_book(ts, b, bq, a, aq)
        if ts >= next_t:
            st = fe.state(ts)
            rows_t.append(ts)
            rows_x.append((st.flow_imbalance * st.sigma, st.imbalance * st.sigma,
                           st.signed_volume_imbalance * st.sigma, st.mid))
            next_t = ts + sample_ms
    t = np.asarray(rows_t, dtype=np.int64)
    X = np.asarray(rows_x, dtype=float)
    valid = t + horizon_ms <= data.book_ts[-1]
    fut = np.searchsorted(data.book_ts, t[valid] + horizon_ms, side="right") - 1
    return X[valid, :3], data.mids[fut] - X[valid, 3]


def _r2(y: np.ndarray, pred: np.ndarray) -> float:
    return float(1.0 - np.sum((y - pred) ** 2) / max(np.sum((y - y.mean()) ** 2), 1e-12))


def fit_fair_value(data: MarketData, hawkes: dict, sigma: float, horizon_ms: int = 2_000,
                   ridge: float = 1e-6) -> dict:
    F, y = _feature_samples(data, hawkes, sigma, horizon_ms)
    coef = np.linalg.solve(F.T @ F + ridge * np.eye(F.shape[1]), F.T @ y)
    return {"b_micro": 0.0, "b_flow": float(coef[0]), "b_obi": float(coef[1]),
            "b_volume": float(coef[2]),
            "r2_in_sample": _r2(y, F @ coef), "horizon_ms": horizon_ms, "samples": int(len(y))}


def fair_value_r2(data: MarketData, calib: dict) -> float:
    """R^2 of the frozen in-sample fair-value model on another window."""
    fv = calib["fair_value"]
    F, y = _feature_samples(data, calib["hawkes"], calib["sigma"], fv["horizon_ms"])
    coef = np.array([fv["b_flow"], fv["b_obi"], fv.get("b_volume", 0.0)])
    return _r2(y, F @ coef)


def select_fair_value(train: MarketData, validation: MarketData, calib: dict,
                      horizons_ms: tuple[int, ...] = (250, 500, 1_000, 2_000, 5_000)) -> dict:
    """Select a prediction horizon on a later fold and gate a harmful model."""
    candidates = []
    for horizon_ms in horizons_ms:
        fair = fit_fair_value(train, calib["hawkes"], calib["sigma"], horizon_ms)
        trial = {**calib, "fair_value": fair}
        candidates.append((fair_value_r2(validation, trial), fair))
    score, selected = max(candidates, key=lambda item: item[0])
    selected = dict(selected)
    selected["validation_r2"] = float(score)
    selected["candidate_r2"] = {
        str(fair["horizon_ms"]): float(r2) for r2, fair in candidates
    }
    selected["enabled"] = bool(score > 0.0)
    if not selected["enabled"]:
        for key in ("b_micro", "b_flow", "b_obi", "b_volume"):
            selected[key] = 0.0
    return selected


def calibrate(data: MarketData, fair_horizon_ms: int = 2_000,
              fair_enabled: bool = True) -> dict:
    sigma = fit_sigma(data)
    arrival = fit_arrival(data)
    hawkes = fit_hawkes(data)
    fair = fit_fair_value(data, hawkes, sigma, horizon_ms=fair_horizon_ms)
    fair["enabled"] = fair_enabled
    if not fair_enabled:
        for key in ("b_micro", "b_flow", "b_obi", "b_volume"):
            fair[key] = 0.0
    return {
        "window": data.name,
        "venue": data.venue,
        "tick_size": data.tick_size,
        "hours": data.hours,
        "sigma": sigma,
        "sigma_bps_per_sqrt_s": sigma / float(np.median(data.mids)) * 1e4,
        "arrival": arrival,
        "hawkes": hawkes,
        "fair_value": fair,
    }


def save(calib: dict, path: str | Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(calib, indent=2))


def load(path: str | Path) -> dict:
    return json.loads(Path(path).read_text())
