"""Univariate Hawkes trade-intensity diagnostics."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class HawkesDiagnostics:
    mu: float
    alpha: float
    beta: float
    branching_ratio: float
    baseline_rate: float
    clustered: bool


def exponential_intensity(
    event_times_ms: np.ndarray | list[int],
    *,
    mu: float,
    alpha: float,
    beta: float,
) -> np.ndarray:
    """Evaluate lambda(t) immediately after each observed event.

    Times are milliseconds; beta is expressed per second and the intensity is
    trades per second. This is a diagnostic estimator, not a fitted model.
    """
    if mu < 0 or alpha < 0 or beta <= 0:
        raise ValueError("mu and alpha must be non-negative; beta must be positive")
    times = np.asarray(event_times_ms, dtype=float)
    if times.size == 0:
        return np.array([], dtype=float)
    if np.any(np.diff(times) < 0):
        raise ValueError("event times must be sorted")
    intensity = np.empty(times.size, dtype=float)
    excitation = 0.0
    previous = times[0]
    for index, timestamp in enumerate(times):
        excitation *= np.exp(-beta * max(timestamp - previous, 0.0) / 1000.0)
        excitation += alpha
        intensity[index] = mu + excitation
        previous = timestamp
    return intensity


def estimate_hawkes_diagnostics(event_times_ms: np.ndarray | list[int]) -> HawkesDiagnostics:
    """Estimate a transparent method-of-moments clustering diagnostic.

    The baseline is the observed mean rate. Alpha is inferred from the ratio
    of short-interval to long-interval event rates; beta uses the median event
    interval. It intentionally reports diagnostics rather than pretending to
    be a maximum-likelihood fit.
    """
    times = np.asarray(event_times_ms, dtype=float)
    if times.size < 2:
        raise ValueError("at least two events are required")
    if np.any(np.diff(times) <= 0):
        raise ValueError("event times must be strictly increasing")
    intervals = np.diff(times) / 1000.0
    duration = (times[-1] - times[0]) / 1000.0
    baseline = (times.size - 1) / duration if duration > 0 else 0.0
    median_interval = float(np.median(intervals))
    beta = 1.0 / max(median_interval, 1e-9)
    short_rate = float(np.mean(intervals <= median_interval)) / max(median_interval, 1e-9)
    alpha = max(short_rate - baseline, 0.0) * 0.5
    branching = min(alpha / beta, 0.999999)
    return HawkesDiagnostics(
        mu=max(baseline * (1.0 - branching), 0.0),
        alpha=alpha,
        beta=beta,
        branching_ratio=branching,
        baseline_rate=baseline,
        clustered=branching > 0.1,
    )
