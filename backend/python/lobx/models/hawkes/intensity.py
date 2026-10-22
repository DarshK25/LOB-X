"""
Univariate exponential Hawkes process.

lambda(t) = mu + sum_{t_i < t} alpha * exp(-beta * (t - t_i))

    mu    = baseline intensity (events / second)
    alpha = jump in intensity per event (events / second)
    beta  = decay rate (1 / second)
Stationary when the branching ratio n = alpha / beta < 1; the long-run mean
intensity is then mu / (1 - n).
"""
from __future__ import annotations

import math

import numpy as np
from numpy.typing import NDArray


class HawkesIntensity:
    """Track lambda(t) for a stream of event times (seconds, non-decreasing)."""

    def __init__(self, mu: float, alpha: float, beta: float) -> None:
        if mu <= 0 or alpha < 0:
            raise ValueError("mu must be positive and alpha non-negative")
        if beta <= 0:
            raise ValueError("beta must be positive (decay rate)")
        if alpha >= beta:
            raise ValueError("Stability requires alpha < beta")
        self._mu = mu
        self._alpha = alpha
        self._beta = beta
        self._last_t: float | None = None
        self._intensity: float = mu

    @property
    def mu(self) -> float:
        return self._mu

    @property
    def alpha(self) -> float:
        return self._alpha

    @property
    def beta(self) -> float:
        return self._beta

    @property
    def branching_ratio(self) -> float:
        return self._alpha / self._beta

    @property
    def stationary_intensity(self) -> float:
        return self._mu / (1.0 - self.branching_ratio)

    @property
    def intensity(self) -> float:
        return self._intensity

    def update(self, t: float) -> float:
        """Register an event at time ``t``; returns lambda just after it."""
        if self._last_t is not None and t > self._last_t:
            decay = math.exp(-self._beta * (t - self._last_t))
            self._intensity = self._mu + (self._intensity - self._mu) * decay
        self._intensity += self._alpha
        self._last_t = t if self._last_t is None else max(self._last_t, t)
        return self._intensity

    def evaluate(self, t: float) -> float:
        """lambda(t) without registering an event."""
        if self._last_t is None or t <= self._last_t:
            return self._intensity
        return self._mu + (self._intensity - self._mu) * math.exp(-self._beta * (t - self._last_t))

    def ratio(self, t: float) -> float:
        """lambda(t) relative to the stationary mean (1.0 = typical)."""
        return self.evaluate(t) / self.stationary_intensity

    def simulate(self, T: float, seed: int | None = None) -> NDArray[np.float64]:
        """Simulate event times on [0, T] with Ogata thinning."""
        rng = np.random.default_rng(seed)
        events: list[float] = []
        t = 0.0
        self._intensity = self._mu
        self._last_t = 0.0
        while t < T:
            lam_bar = self.evaluate(t) + self._alpha
            t_prop = t + rng.exponential(1.0 / lam_bar)
            if t_prop > T:
                break
            if rng.random() < self.evaluate(t_prop) / lam_bar:
                events.append(t_prop)
                self.update(t_prop)
            t = t_prop
        return np.array(events)


def regime(ratio: float) -> str:
    """Label used by the live gauge and by the quoting logic."""
    if ratio < 0.7:
        return "QUIET"
    if ratio < 1.5:
        return "NORMAL"
    if ratio < 3.0:
        return "ELEVATED"
    return "BURST"
