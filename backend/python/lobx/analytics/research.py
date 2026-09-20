"""Research metrics for quote quality, calibration, and strategy comparison."""
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import stats


def _mean_or_zero(values: list[float]) -> float:
    return float(np.mean(values)) if values else 0.0


@dataclass(frozen=True)
class CalibrationResult:
    model: str
    usable: bool
    r_squared: float
    parameters: dict[str, float]
    reason: str | None = None


def quote_quality(events: Iterable[dict], fills: Iterable[dict], *, start: int, end: int) -> dict:
    """Measure quote exposure from the shared event ledger.

    Quote lifetime is measured per order from ``ORDER_CREATED`` to cancellation,
    replacement, fill, or session end. No unobserved exposure is inferred.
    """
    events = list(events)
    fills = list(fills)
    orders: dict[int, dict] = {}
    lifetimes: list[int] = []
    replacements = 0
    quoted_ms = 0
    for event in events:
        kind = event.get("event_type")
        order_id = event.get("order_id")
        if kind == "ORDER_CREATED":
            orders[int(order_id)] = event
        elif kind in {"ORDER_CANCELED", "FILL"} and order_id is not None:
            order = orders.pop(int(order_id), None)
            if order is not None:
                lifetime = max(int(event["timestamp"]) - int(order["timestamp"]), 0)
                lifetimes.append(lifetime)
                quoted_ms += lifetime
            if kind == "ORDER_CANCELED" and event.get("reason") == "quote_replaced":
                replacements += 1
    for order in orders.values():
        lifetime = max(end - int(order["timestamp"]), 0)
        lifetimes.append(lifetime)
        quoted_ms += lifetime
    exposure_ms = max(end - start, 0)
    return {
        "spread": _mean_or_zero([
            event["ask_price"] - event["bid_price"]
            for event in events
            if event.get("event_type") == "QUOTE_CALCULATED"
            and event.get("bid_price") is not None and event.get("ask_price") is not None
        ]),
        "quote_lifetime_ms_mean": float(np.mean(lifetimes) if lifetimes else 0.0),
        "quote_lifetime_ms_total": quoted_ms,
        "replacements": replacements,
        "percentage_time_quoted": quoted_ms / (exposure_ms * 2) if exposure_ms else 0.0,
        "fill_probability": len(fills) / len(lifetimes) if lifetimes else 0.0,
        "fill_rate_per_quote_second": len(fills) / (quoted_ms / 1000.0) if quoted_ms else 0.0,
        "distance_from_mid_mean": _mean_or_zero([
            abs(event["bid_price"] - event["market_mid"])
            for event in events
            if event.get("event_type") == "QUOTE_CALCULATED"
            and event.get("bid_price") is not None
        ]),
        "queue_position_mean": _mean_or_zero([
            event.get("queue_ahead_qty", 0.0)
            for event in events if event.get("event_type") == "QUEUE_UPDATED"
        ]),
    }


def compare_fill_models(curve: pd.DataFrame, *, min_points: int = 2, min_r_squared: float = 0.50) -> dict[str, CalibrationResult]:
    """Compare exponential, power-law, and empirical lookup models.

    The exponential result is usable only when its configured R-squared gate
    passes. Alternative models are reported for investigation, never silently
    substituted into the AS strategy.
    """
    data = curve.loc[
        (curve["fill_rate_per_quote_second"] > 0)
        & (curve["distance_usd"] > 0),
        ["distance_usd", "fill_rate_per_quote_second"],
    ].dropna()
    if len(data) < min_points:
        reason = f"need at least {min_points} populated buckets"
        return {name: CalibrationResult(name, False, 0.0, {}, reason) for name in ("exponential", "power_law", "empirical_lookup")}
    distance = data.distance_usd.to_numpy(float)
    rate = data.fill_rate_per_quote_second.to_numpy(float)
    exponential = stats.linregress(distance, np.log(rate))
    exp_r2 = float(exponential.rvalue ** 2)
    exp_ok = exponential.slope < 0 and exp_r2 >= min_r_squared
    power = stats.linregress(np.log(distance), np.log(rate))
    power_r2 = float(power.rvalue ** 2)
    return {
        "exponential": CalibrationResult(
            "exponential", exp_ok, exp_r2,
            {"A": float(np.exp(exponential.intercept)), "kappa": float(-exponential.slope)},
            None if exp_ok else "STOP: exponential fit failed slope or R-squared gate",
        ),
        "power_law": CalibrationResult(
            "power_law", power.slope < 0, power_r2,
            {"A": float(np.exp(power.intercept)), "exponent": float(-power.slope)},
            None if power.slope < 0 else "rate does not decay with distance",
        ),
        "empirical_lookup": CalibrationResult(
            "empirical_lookup", True, 1.0,
            {"buckets": float(len(data))},
            "lookup only; not a parametric AS fit",
        ),
    }


def gamma_sweep(
    gammas: Iterable[float],
    evaluate: Callable[[float], dict[str, float]],
) -> pd.DataFrame:
    """Evaluate gamma values without selecting one or changing a gate."""
    rows = []
    for gamma in gammas:
        if gamma <= 0:
            raise ValueError("gamma values must be positive")
        metrics = dict(evaluate(float(gamma)))
        metrics["gamma"] = float(gamma)
        rows.append(metrics)
    return pd.DataFrame(rows).sort_values("gamma").reset_index(drop=True)


def compare_strategies(
    replay: object,
    depth: pd.DataFrame,
    trades: pd.DataFrame,
    strategies: dict[str, Callable[[], object]],
) -> pd.DataFrame:
    """Run named strategies over the exact same market event tables."""
    rows = []
    for name, factory in strategies.items():
        result = replay.run(depth.copy(), trades.copy(), factory)
        row = dict(result.summary)
        row["strategy"] = name
        rows.append(row)
    return pd.DataFrame(rows)


def conditional_microprice_returns(
    observations: pd.DataFrame,
    horizons_ms: Iterable[int] = (100, 500, 1_000, 5_000),
) -> pd.DataFrame:
    """Measure forward midpoint returns conditional on microprice deviation sign."""
    required = {"event_time", "mid", "microprice"}
    if not required.issubset(observations.columns):
        raise ValueError(f"observations missing columns: {sorted(required - set(observations.columns))}")
    data = observations.sort_values("event_time", kind="stable").reset_index(drop=True).copy()
    data["deviation"] = data["microprice"] - data["mid"]
    rows: list[dict[str, float | int | str]] = []
    times = data.event_time.to_numpy(dtype=np.int64)
    mids = data.mid.to_numpy(dtype=float)
    for horizon in horizons_ms:
        for sign, mask in (("positive", data.deviation > 0), ("negative", data.deviation < 0)):
            returns = []
            for index in np.flatnonzero(mask.to_numpy()):
                future = np.searchsorted(times, times[index] + horizon, side="left")
                if future < len(mids):
                    returns.append(mids[future] - mids[index])
            rows.append({
                "horizon_ms": int(horizon), "condition": sign,
                "observations": len(returns),
                "mean_forward_return": float(np.mean(returns)) if returns else float("nan"),
            })
    return pd.DataFrame(rows)
