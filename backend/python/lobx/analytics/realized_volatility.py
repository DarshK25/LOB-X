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
from typing import Sequence
from lobx.analytics.returns import compute_log_returns


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
