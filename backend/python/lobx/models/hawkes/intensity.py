"""
Hawkes Process Intensity.

λ(t) = μ + Σ_{t_i < t} α * exp(-β * (t - t_i))

Where:
    μ   = baseline (background) intensity
    α   = excitation amplitude (jump size on each event)
    β   = decay rate (memory length)
"""
from __future__ import annotations
import numpy as np
from numpy.typing import NDArray


class HawkesIntensity:
    """
    Univariate exponential Hawkes process.

    Tracks the conditional intensity λ(t) for a stream of events (e.g., trades).
    """

    def __init__(self, mu: float, alpha: float, beta: float) -> None:
        if beta <= 0:
            raise ValueError("beta must be positive (decay rate)")
        if alpha >= beta:
            raise ValueError("Stability requires alpha < beta")
        self._mu    = mu
        self._alpha = alpha
        self._beta  = beta
        self._last_t: float = 0.0
        self._intensity: float = mu

    @property
    def intensity(self) -> float:
        return self._intensity

    def update(self, t: float) -> float:
        """
        Register a new event at time `t`. Updates and returns λ(t).

        Parameters
        ----------
        t : event timestamp (seconds, monotonically increasing)

        Returns
        -------
        λ(t) immediately after the event
        """
        dt = t - self._last_t
        # Decay existing intensity.
        self._intensity = (
            self._mu
            + (self._intensity - self._mu) * np.exp(-self._beta * dt)
            + self._alpha
        )
        self._last_t = t
        return self._intensity

    def evaluate(self, t: float) -> float:
        """Evaluate λ at time `t` WITHOUT registering an event."""
        dt = t - self._last_t
        return self._mu + (self._intensity - self._mu) * np.exp(-self._beta * dt)

    def simulate(self, T: float, seed: int | None = None) -> NDArray[np.float64]:
        """
        Simulate event times on [0, T] using Ogata's thinning algorithm.

        Returns
        -------
        Array of event timestamps.
        """
        rng    = np.random.default_rng(seed)
        events = []
        t      = 0.0
        self._intensity = self._mu
        self._last_t    = 0.0

        while t < T:
            lam_bar = self.evaluate(t) + self._alpha  # upper bound
            dt_prop = rng.exponential(1.0 / lam_bar)
            t_prop  = t + dt_prop
            if t_prop > T:
                break
            lam_t = self.evaluate(t_prop)
            if rng.random() < lam_t / lam_bar:
                events.append(t_prop)
                self.update(t_prop)
            t = t_prop

        return np.array(events)
