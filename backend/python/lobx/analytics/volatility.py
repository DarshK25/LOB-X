"""
analytics/volatility.py — EWMA mid-price volatility estimator.

Uses the RiskMetrics (JP Morgan, 1994) EWMA formula:
  variance_t = λ * variance_{t-1} + (1-λ) * r_t²

where r_t = log(mid_t / mid_{t-1}) is the log-return between consecutive
depth events, and λ (decay) controls the smoothing.

λ = 0.94 is the RiskMetrics standard for daily data.
λ = 0.98 is more appropriate for high-frequency tick data where you want
less jitter from individual outlier ticks.

This is a PLACEHOLDER estimate until Phase 3 calibrates σ properly from
the collected depth + trade Parquet data.  Do not read the exact value
into any live risk limit — treat it as a qualitative indicator until then.

Why here and not in __init__.py?
─────────────────────────────────
The Phase 0 codebase had volatility logic embedded in model __init__s.
Extracting it to analytics/ means:
  1. Unit-testable in isolation (test_volatility.py can import this
     without instantiating a Collector or a Strategy).
  2. Reusable across AS strategy, Almgren-Chriss executor, risk module.
  3. Easy to swap for a realized-variance or Parkinson estimator in Phase 3.
"""
from __future__ import annotations

import math


class EWMAVolatilityEstimator:
    """Rolling EWMA volatility over mid-price log-returns.

    Parameters
    ----------
    decay : float in (0, 1).  Higher → slower/smoother response.
            0.98 is a reasonable starting point for 100ms depth ticks.

    Usage
    -----
        estimator = EWMAVolatilityEstimator(decay=0.98)
        for mid_price in stream:
            vol = estimator.update(mid_price)
    """

    def __init__(self, decay: float = 0.98) -> None:
        if not 0.0 < decay < 1.0:
            raise ValueError(f"decay must be in (0, 1), got {decay}")
        self.decay       = decay
        self._last_mid:  float | None = None
        self._variance:  float = 0.0
        self._n_updates: int   = 0   # track how many updates received

    def update(self, mid_price: float) -> float:
        """Feed one mid-price observation.  Returns current volatility estimate.

        The first call seeds the estimator and returns 0.0 (no return yet).
        From the second call onwards, returns sqrt(EWMA variance).

        Parameters
        ----------
        mid_price : current (best_bid + best_ask) / 2

        Returns
        -------
        Volatility estimate as a positive float, or 0.0 before warm-up.
        """
        if mid_price <= 0.0:
            return self.current()

        if self._last_mid is None or self._last_mid <= 0.0:
            self._last_mid = mid_price
            return 0.0

        log_return = math.log(mid_price / self._last_mid)
        self._variance = (
            self.decay * self._variance
            + (1.0 - self.decay) * log_return ** 2
        )
        self._last_mid = mid_price
        self._n_updates += 1
        return self.current()

    def current(self) -> float:
        """Return the latest volatility without consuming a new observation."""
        return math.sqrt(self._variance) if self._n_updates > 0 else 0.0

    @property
    def is_warmed_up(self) -> bool:
        """True once at least one log-return has been computed."""
        return self._n_updates > 0

    def reset(self) -> None:
        """Reset state — useful for backtester replays that need a fresh start."""
        self._last_mid  = None
        self._variance  = 0.0
        self._n_updates = 0
