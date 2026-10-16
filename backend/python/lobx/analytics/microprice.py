"""
analytics/microprice.py — Microprice computation and predictive power evaluation.

Reference
---------
Stoikov (2018) "The Micro-Price: A High-Frequency Estimator of Future Prices"

Microprice adjusts the mid-price by weighting the best bid and best ask
using the opposite side's volume:
  Microprice = (Ask * BidVolume + Bid * AskVolume) / (BidVolume + AskVolume)
"""
from __future__ import annotations

from typing import Any
import numpy as np
import pandas as pd

try:
    import lobx_cpp
except ImportError:
    lobx_cpp = None


def compute_microprice(book: Any) -> float | None:
    """Compute the microprice from the current state of the order book."""
    if book is None:
        return None
    bid_price = getattr(book, "best_bid", None)
    if callable(bid_price):
        bid_price = bid_price()
    ask_price = getattr(book, "best_ask", None)
    if callable(ask_price):
        ask_price = ask_price()

    if bid_price is None or ask_price is None:
        return None

    bid_qty = getattr(book, "best_bid_qty", None)
    if callable(bid_qty):
        bid_qty = bid_qty() or 0.0
    ask_qty = getattr(book, "best_ask_qty", None)
    if callable(ask_qty):
        ask_qty = ask_qty() or 0.0

    total_qty = bid_qty + ask_qty
    if total_qty == 0.0:
        return (bid_price + ask_price) / 2.0

    return (ask_price * bid_qty + bid_price * ask_qty) / total_qty


class MicropriceMonitor:
    """Tracks time-series of microprice and its deviation from mid-price."""

    def __init__(self) -> None:
        self.current_microprice: float | None = None
        self.current_mid: float | None = None

    def update(self, book: Any) -> None:
        """Update current values from the order book."""
        bid = book.best_bid() if callable(getattr(book, "best_bid", None)) else None
        ask = book.best_ask() if callable(getattr(book, "best_ask", None)) else None

        if bid is None or ask is None:
            self.current_microprice = None
            self.current_mid = None
            return

        self.current_microprice = compute_microprice(book)
        self.current_mid = (bid + ask) / 2.0

    @property
    def drift(self) -> float | None:
        if self.current_microprice is not None and self.current_mid is not None:
            return self.current_microprice - self.current_mid
        return None


def evaluate_microprice_predictive_power(
    records: list[dict[str, Any]],
    horizons_ms: tuple[int, ...] = (100, 500, 1000, 5000, 10000),
) -> dict[str, Any]:
    """Evaluate empirical correlation, directional accuracy, and conditional returns.

    Expects records with: timestamp, mid, microprice_deviation.
    """
    if not records:
        return {"count": 0, "horizons": {}}

    df = pd.DataFrame(records).sort_values("timestamp").reset_index(drop=True)
    if "microprice_deviation" not in df.columns or "mid" not in df.columns:
        return {"count": len(df), "horizons": {}}

    results = {}
    timestamps = df["timestamp"].values
    mids = df["mid"].values
    deviations = df["microprice_deviation"].values

    for h in horizons_ms:
        future_returns = []
        dev_sample = []

        for i in range(len(df)):
            t_target = timestamps[i] + h
            # Find future mid index via searchsorted
            idx = np.searchsorted(timestamps, t_target)
            if idx < len(df):
                f_mid = mids[idx]
                ret = f_mid - mids[i]
                future_returns.append(ret)
                dev_sample.append(deviations[i])

        if len(future_returns) > 1:
            rets = np.array(future_returns, dtype=float)
            devs = np.array(dev_sample, dtype=float)

            corr = float(np.corrcoef(devs, rets)[0, 1]) if np.std(devs) > 0 and np.std(rets) > 0 else 0.0
            dir_acc = float(np.mean(np.sign(devs) == np.sign(rets))) if len(rets) > 0 else 0.0

            # Bucket analysis
            buckets = {
                "<-2.0": float(np.mean(rets[devs < -2.0])) if np.any(devs < -2.0) else 0.0,
                "-2.0 to -1.0": float(np.mean(rets[(devs >= -2.0) & (devs < -1.0)])) if np.any((devs >= -2.0) & (devs < -1.0)) else 0.0,
                "-1.0 to 0.0": float(np.mean(rets[(devs >= -1.0) & (devs < 0.0)])) if np.any((devs >= -1.0) & (devs < 0.0)) else 0.0,
                "0.0 to +1.0": float(np.mean(rets[(devs >= 0.0) & (devs <= 1.0)])) if np.any((devs >= 0.0) & (devs <= 1.0)) else 0.0,
                "+1.0 to +2.0": float(np.mean(rets[(devs > 1.0) & (devs <= 2.0)])) if np.any((devs > 1.0) & (devs <= 2.0)) else 0.0,
                "> +2.0": float(np.mean(rets[devs > 2.0])) if np.any(devs > 2.0) else 0.0,
            }

            results[f"{h}ms"] = {
                "correlation": corr,
                "directional_accuracy": dir_acc,
                "conditional_return_buckets": buckets,
            }

    return {"count": len(df), "horizons": results}
