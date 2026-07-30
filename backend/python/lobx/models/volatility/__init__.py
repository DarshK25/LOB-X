"""Volatility models sub-package (EWMA, GARCH stub)."""
from __future__ import annotations
import math


class EWMAVolatility:
    """
    Exponentially-Weighted Moving Average volatility estimator.

    σ²_t = λ * σ²_{t-1} + (1 - λ) * r²_t
    """

    def __init__(self, lambda_: float = 0.94, init_var: float = 1.0) -> None:
        if not 0 < lambda_ < 1:
            raise ValueError("lambda_ must be in (0, 1)")
        self._lambda = lambda_
        self._var    = init_var
        self._prev_mid: float | None = None

    def update(self, mid: float) -> float:
        """
        Update with a new mid-price observation.
        Returns current volatility estimate (std dev).
        """
        if self._prev_mid is not None:
            r2 = (mid - self._prev_mid) ** 2
            self._var = self._lambda * self._var + (1 - self._lambda) * r2
        self._prev_mid = mid
        return math.sqrt(self._var)

    @property
    def sigma(self) -> float:
        return math.sqrt(self._var)

    @property
    def variance(self) -> float:
        return self._var
