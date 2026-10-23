"""Deterministic bootstrap summaries for multi-session research reports."""
from __future__ import annotations

from collections.abc import Callable, Sequence
import math

import numpy as np


def bootstrap_mean_ci(
    values: Sequence[float],
    *,
    replications: int = 2_000,
    confidence: float = 0.95,
    seed: int = 42,
    statistic: Callable[[np.ndarray], float] | None = None,
) -> dict[str, float | int | None]:
    """Return a percentile bootstrap CI for a session-level statistic.

    Resampling is performed at session level rather than fill level because
    fills within one market session are dependent observations.
    """
    data = np.asarray(
        [float(value) for value in values if math.isfinite(float(value))],
        dtype=float,
    )
    if replications <= 0:
        raise ValueError("replications must be positive")
    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence must be strictly between 0 and 1")
    if data.size == 0:
        return {
            "observations": 0,
            "estimate": None,
            "lower": None,
            "upper": None,
            "confidence": confidence,
            "replications": 0,
        }

    fn = statistic or (lambda sample: float(np.mean(sample)))
    estimate = float(fn(data))
    if data.size == 1:
        return {
            "observations": 1,
            "estimate": estimate,
            "lower": estimate,
            "upper": estimate,
            "confidence": confidence,
            "replications": 0,
        }

    rng = np.random.default_rng(seed)
    samples = rng.integers(0, data.size, size=(replications, data.size))
    statistics = np.asarray([fn(data[indexes]) for indexes in samples], dtype=float)
    alpha = (1.0 - confidence) / 2.0
    return {
        "observations": int(data.size),
        "estimate": estimate,
        "lower": float(np.quantile(statistics, alpha)),
        "upper": float(np.quantile(statistics, 1.0 - alpha)),
        "confidence": confidence,
        "replications": int(replications),
    }


__all__ = ["bootstrap_mean_ci"]
