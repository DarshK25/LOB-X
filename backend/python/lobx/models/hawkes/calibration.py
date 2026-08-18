"""
Hawkes Process MLE Calibration.

Calibrates (μ, α, β) by maximising the log-likelihood of observed event times.
"""
from __future__ import annotations
import numpy as np
from numpy.typing import NDArray
from scipy.optimize import minimize


def log_likelihood(params: NDArray[np.float64], events: NDArray[np.float64], T: float) -> float:
    """Negative log-likelihood of exponential Hawkes process."""
    mu, alpha, beta = params
    if mu <= 0 or alpha <= 0 or beta <= 0 or alpha >= beta:
        return np.inf

    n      = len(events)
    ll     = -mu * T

    # Recursive computation of R_i = Σ_{j<i} exp(-β(t_i - t_j))
    r = 0.0
    for i in range(n):
        if i > 0:
            r = np.exp(-beta * (events[i] - events[i - 1])) * (1 + r)
        lam_i = mu + alpha * r
        if lam_i <= 0:
            return np.inf
        ll += np.log(lam_i)

    # Integral correction term
    integral = np.sum(1.0 - np.exp(-beta * (T - events)))
    ll -= (alpha / beta) * integral

    return -ll  # minimise negative log-likelihood


def calibrate(
    events: NDArray[np.float64],
    T: float,
    n_starts: int = 5,
    seed: int = 42,
) -> dict[str, float]:
    """
    Calibrate Hawkes parameters via MLE.

    Parameters
    ----------
    events   : sorted array of event timestamps
    T        : observation window length
    n_starts : number of random restarts for robustness
    seed     : RNG seed

    Returns
    -------
    dict with keys 'mu', 'alpha', 'beta'
    """
    rng     = np.random.default_rng(seed)
    best    = None
    best_ll = np.inf

    for _ in range(n_starts):
        x0 = rng.uniform(0.01, 2.0, size=3)
        result = minimize(
            log_likelihood,
            x0,
            args=(events, T),
            method="L-BFGS-B",
            bounds=[(1e-6, None), (1e-6, None), (1e-6, None)],
        )
        if result.fun < best_ll:
            best_ll = result.fun
            best    = result.x

    if best is None:
        raise RuntimeError("Calibration failed — check event data")

    return {"mu": float(best[0]), "alpha": float(best[1]), "beta": float(best[2])}
