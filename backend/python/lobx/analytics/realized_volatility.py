"""
analytics/realized_volatility.py — Realized Volatility estimators.

Reference
---------
Andersen, Bollerslev, Diebold, and Labys (2001) 
"The distribution of realized exchange rate volatility"
Journal of the American Statistical Association.

Realized Volatility (RV) is computed as the sum of squared high-frequency
returns over a specific period. It provides a non-parametric estimate
of the price variation.
"""
from __future__ import annotations

import math
from collections import deque
from typing import Sequence


def compute_realized_variance(returns: Sequence[float]) -> float:
    """Compute realized variance (sum of squared returns)."""
    return sum(r * r for r in returns)


def compute_realized_volatility(returns: Sequence[float], annualization_factor: float = 1.0) -> float:
    """Compute realized volatility from a sequence of returns.
    
    Parameters
    ----------
    returns : sequence of returns (usually log-returns)
    annualization_factor : factor to scale variance (e.g., 252 * 24 * 60 for minute data to annual)
    """
    if not returns:
        return 0.0
    rv2 = compute_realized_variance(returns)
    return math.sqrt(rv2 * annualization_factor)


class RollingRealizedVolatility:
    """Tracks Realized Volatility over a rolling window of returns."""

    def __init__(self, window: int = 100, annualization_factor: float = 1.0) -> None:
        from collections import deque
        self._window = window
        self._factor = annualization_factor
        self._returns: deque[float] = deque(maxlen=window)
        self._rv2_sum: float = 0.0

    def update(self, return_val: float) -> float:
        """Add a return and return the current RV.
        
        Optimized to O(1) by maintaining a running sum of squares.
        """
        if len(self._returns) == self._window:
            oldest = self._returns.popleft()
            self._rv2_sum -= (oldest * oldest)
            # Handle float precision drift
            if self._rv2_sum < 0:
                self._rv2_sum = 0.0
                
        self._returns.append(return_val)
        self._rv2_sum += (return_val * return_val)
        
        return math.sqrt(self._rv2_sum * self._factor)

    @property
    def current_rv(self) -> float:
        return math.sqrt(self._rv2_sum * self._factor)


class TimestampAwareRollingRealizedVolatility:
    """Rolling realized volatility over an elapsed-time window.

    Unlike :class:`RollingRealizedVolatility`, the window is measured in
    exchange timestamp milliseconds rather than in observations.  This keeps
    a one-minute estimate comparable when depth-event frequency changes and
    avoids treating several updates with the same timestamp as elapsed time.

    The default result is realized log-volatility per sqrt(second):
    ``sqrt(sum(log_return**2) / elapsed_seconds)``.  This makes the result
    comparable to calibration values expressed as log-return per sqrt(second).
    ``annualization_factor`` is available for callers that need a different
    scale.
    """

    def __init__(
        self,
        window_ms: int = 60_000,
        annualization_factor: float = 1.0,
    ) -> None:
        if window_ms <= 0:
            raise ValueError("window_ms must be positive")
        if annualization_factor < 0.0 or not math.isfinite(annualization_factor):
            raise ValueError("annualization_factor must be finite and non-negative")
        self.window_ms = int(window_ms)
        self.annualization_factor = float(annualization_factor)
        # Each entry is (return_end_timestamp_ms, log_return, interval_seconds).
        self._observations: deque[tuple[int, float, float]] = deque()
        self._rv2_sum = 0.0
        self._elapsed_seconds = 0.0
        self._last_timestamp: int | None = None
        self._last_mid: float | None = None
        self._last_log_return = 0.0

    def update(self, timestamp: int, mid_price: float) -> float:
        """Add one timestamped mid-price and return the current estimate.

        Non-positive prices and out-of-order timestamps are ignored.  The
        latter is important for live depth callbacks, where a delayed message
        must not make the rolling window move backwards.
        """
        timestamp = int(timestamp)
        mid_price = float(mid_price)
        if mid_price <= 0.0 or not math.isfinite(mid_price):
            return self.current()
        if self._last_timestamp is not None and timestamp <= self._last_timestamp:
            return self.current()
        if self._last_mid is None:
            self._last_timestamp = timestamp
            self._last_mid = mid_price
            return self.current()

        interval_seconds = (timestamp - self._last_timestamp) / 1000.0
        log_return = math.log(mid_price / self._last_mid)
        self._last_timestamp = timestamp
        self._last_mid = mid_price
        self._last_log_return = log_return
        self._observations.append((timestamp, log_return, interval_seconds))
        self._rv2_sum += log_return * log_return
        self._elapsed_seconds += interval_seconds

        cutoff = timestamp - self.window_ms
        while self._observations and self._observations[0][0] < cutoff:
            _, oldest_return, oldest_interval = self._observations.popleft()
            self._rv2_sum -= oldest_return * oldest_return
            self._elapsed_seconds -= oldest_interval
        # Protect against tiny negative values after repeated subtraction.
        self._rv2_sum = max(self._rv2_sum, 0.0)
        self._elapsed_seconds = max(self._elapsed_seconds, 0.0)
        return self.current()

    def current(self) -> float:
        """Return the current rolling realized-volatility estimate."""
        if self._elapsed_seconds <= 0.0:
            return 0.0
        return math.sqrt(
            (self._rv2_sum / self._elapsed_seconds) * self.annualization_factor
        )

    @property
    def last_log_return(self) -> float:
        """Return the latest accepted log return."""
        return self._last_log_return

    @property
    def last_timestamp(self) -> int | None:
        return self._last_timestamp

    @property
    def observation_count(self) -> int:
        return len(self._observations)

    @property
    def is_warmed_up(self) -> bool:
        return bool(self._observations)

    def reset(self) -> None:
        """Clear all timestamp, price, and rolling-variance state."""
        self._observations.clear()
        self._rv2_sum = 0.0
        self._elapsed_seconds = 0.0
        self._last_timestamp = None
        self._last_mid = None
        self._last_log_return = 0.0
