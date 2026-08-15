"""
analytics/returns.py — Log-return and simple-return computations.

Reference
---------
Campbell, Lo & MacKinlay (1997) "The Econometrics of Financial Markets",
Chapter 1 — Definitions and structure of financial time series.

Log returns are preferred in financial analytics because:
  1. They are time-additive: r_total = r_1 + r_2 + ... + r_n
  2. They are approximately normally distributed for short intervals
  3. They prevent negative prices in continuous-time models

For high-frequency data (100ms Binance depth), log returns are computed
as log(mid_t / mid_{t-1}) where mid = (best_bid + best_ask) / 2.
"""
from __future__ import annotations

import math
from collections import deque
from typing import Sequence


def log_return(price_now: float, price_prev: float) -> float:
    """Compute a single log-return.

    r_t = log(P_t / P_{t-1})

    Parameters
    ----------
    price_now  : current price
    price_prev : previous price

    Returns
    -------
    Log-return as float. Returns 0.0 if either price is <= 0.
    """
    if price_now <= 0 or price_prev <= 0:
        return 0.0
    return math.log(price_now / price_prev)


def simple_return(price_now: float, price_prev: float) -> float:
    """Compute a simple (arithmetic) return.

    r_t = (P_t - P_{t-1}) / P_{t-1}

    Returns 0.0 if price_prev <= 0.
    """
    if price_prev <= 0:
        return 0.0
    return (price_now - price_prev) / price_prev


class RollingReturnSeries:
    """Maintain a rolling window of log-returns from a price stream.

    Usage
    -----
        series = RollingReturnSeries(window=500)
        for mid_price in stream:
            series.update(mid_price)
        mean  = series.mean()
        stdev = series.std()
    """

    def __init__(self, window: int = 500) -> None:
        self._window    = window
        self._prices:  deque[float] = deque(maxlen=window + 1)
        self._returns: deque[float] = deque(maxlen=window)

    def update(self, price: float) -> float | None:
        """Feed one price observation. Returns latest log-return or None."""
        if price <= 0:
            return None
        self._prices.append(price)
        if len(self._prices) < 2:
            return None
        r = log_return(self._prices[-1], self._prices[-2])
        self._returns.append(r)
        return r

    def mean(self) -> float | None:
        if not self._returns:
            return None
        return sum(self._returns) / len(self._returns)

    def std(self) -> float | None:
        """Sample standard deviation of returns in the window."""
        if len(self._returns) < 2:
            return None
        mu = self.mean()
        variance = sum((r - mu) ** 2 for r in self._returns) / (len(self._returns) - 1)
        return math.sqrt(variance)

    def values(self) -> list[float]:
        return list(self._returns)

    def __len__(self) -> int:
        return len(self._returns)


def compute_log_returns(prices: Sequence[float]) -> list[float]:
    """Batch-compute log-returns from a price sequence.

    Parameters
    ----------
    prices : sequence of prices (e.g., list of mid-prices from Parquet)

    Returns
    -------
    List of length len(prices)-1. Empty if len(prices) < 2.
    """
    if len(prices) < 2:
        return []
    return [
        log_return(prices[i], prices[i - 1])
        for i in range(1, len(prices))
        if prices[i] > 0 and prices[i - 1] > 0
    ]
