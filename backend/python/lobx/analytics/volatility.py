"""Mid-price volatility in the units the quoting models need: USD per sqrt(second).

Avellaneda-Stoikov and GLFT both use ``sigma`` as the diffusion coefficient of
the mid price, ``dS = sigma dW``, so ``sigma**2 * tau`` is a price variance in
USD^2 when ``tau`` is in seconds.  A per-update log return (about 3e-6 for BTC)
is dimensionless and depends on how often the feed happens to update, so it
cannot be plugged into those formulas.

The estimator samples the mid on a fixed clock (default 1 s) to avoid the
bid-ask bounce that dominates tick-by-tick changes, then applies an EWMA with
a half-life measured in seconds of event time.
"""
from __future__ import annotations

import math

import numpy as np


class EWMAVolatility:
    """Time-aware EWMA of squared mid changes on a fixed sampling grid.

    Parameters
    ----------
    sample_s   : sampling interval in seconds (the variance is per this interval
                 and is converted to per-second on output).
    halflife_s : EWMA half-life in seconds of event time.
    floor      : lower bound returned before warm-up, USD/sqrt(s).
    """

    def __init__(self, sample_s: float = 1.0, halflife_s: float = 120.0,
                 floor: float = 0.0) -> None:
        if sample_s <= 0 or halflife_s <= 0:
            raise ValueError("sample_s and halflife_s must be positive")
        self.sample_ms = int(sample_s * 1000)
        self.sample_s = sample_s
        self.alpha = 1.0 - math.exp(-math.log(2.0) * sample_s / halflife_s)
        self.floor = floor
        self._var: float | None = None
        self._grid_t: int | None = None
        self._grid_mid: float | None = None
        self._last_mid: float | None = None
        self.samples = 0

    def update(self, timestamp_ms: int, mid: float) -> float:
        """Feed one mid observation; returns sigma in USD/sqrt(s)."""
        if mid <= 0:
            return self.sigma
        if self._grid_t is None:
            self._grid_t = timestamp_ms - timestamp_ms % self.sample_ms
            self._grid_mid = mid
            self._last_mid = mid
            return self.sigma
        while timestamp_ms >= self._grid_t + self.sample_ms:
            # The price at a grid boundary is the last mid observed before it.
            change = self._last_mid - self._grid_mid
            sq = change * change
            self._var = sq if self._var is None else self._var + self.alpha * (sq - self._var)
            self._grid_mid = self._last_mid
            self._grid_t += self.sample_ms
            self.samples += 1
            if timestamp_ms >= self._grid_t + 600 * self.sample_ms:
                # Long gap: skip ahead instead of looping over empty samples.
                self._grid_t = timestamp_ms - timestamp_ms % self.sample_ms
        self._last_mid = mid
        return self.sigma

    @property
    def sigma(self) -> float:
        """USD per sqrt(second)."""
        if self._var is None:
            return self.floor
        return max(math.sqrt(self._var / self.sample_s), self.floor)

    @property
    def is_warm(self) -> bool:
        return self.samples >= 30

    def seed(self, sigma: float) -> None:
        """Start from a calibrated value instead of zero."""
        self._var = sigma * sigma * self.sample_s


def realized_sigma(timestamps_ms: np.ndarray, mids: np.ndarray, sample_s: float = 1.0) -> float:
    """Realized volatility over a whole window, USD per sqrt(second).

    Mids are sampled at the last observation before each grid point
    (previous-tick sampling), then sigma^2 = mean(dS^2) / sample_s.
    """
    if len(mids) < 2:
        return 0.0
    step = int(sample_s * 1000)
    grid = np.arange(timestamps_ms[0] - timestamps_ms[0] % step + step, timestamps_ms[-1], step)
    if len(grid) < 2:
        return 0.0
    idx = np.searchsorted(timestamps_ms, grid, side="right") - 1
    sampled = mids[idx]
    changes = np.diff(sampled)
    return float(math.sqrt(np.mean(changes * changes) / sample_s))
