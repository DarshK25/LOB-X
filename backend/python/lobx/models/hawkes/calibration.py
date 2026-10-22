"""
Exponential Hawkes maximum-likelihood calibration.

log L = -mu*T - (alpha/beta) * sum_i (1 - exp(-beta (T - t_i)))
        + sum_i log(mu + alpha * R_i),   R_i = exp(-beta dt_i) (1 + R_{i-1})

The optimiser works on log-parameters with the branching ratio n = alpha/beta
mapped through a logistic, so every trial point is stationary.
"""
from __future__ import annotations

import math

import numpy as np
from numpy.typing import NDArray
from scipy.optimize import minimize


def _neg_loglik(theta: NDArray[np.float64], dts: list[float], tail: NDArray[np.float64],
                T: float) -> float:
    mu = math.exp(theta[0])
    beta = math.exp(theta[2])
    n = 1.0 / (1.0 + math.exp(-theta[1]))
    alpha = n * beta
    log = math.log
    exp = math.exp
    r = 0.0
    ll = -mu * T + log(mu)
    for dt in dts[1:]:
        r = exp(-beta * dt) * (1.0 + r)
        ll += log(mu + alpha * r)
    ll -= n * float(np.sum(1.0 - np.exp(-beta * tail)))
    return -ll


def log_likelihood(params: NDArray[np.float64], events: NDArray[np.float64], T: float) -> float:
    """Negative log-likelihood for (mu, alpha, beta); inf outside the stable region."""
    mu, alpha, beta = params
    if mu <= 0 or alpha <= 0 or beta <= 0 or alpha >= beta:
        return np.inf
    theta = np.array([math.log(mu), math.log((alpha / beta) / (1 - alpha / beta)), math.log(beta)])
    events = np.asarray(events, dtype=float)
    dts = np.diff(events, prepend=events[0]).tolist()
    return _neg_loglik(theta, dts, T - events, T)


def calibrate(
    events: NDArray[np.float64],
    T: float | None = None,
    n_starts: int = 4,
    seed: int = 42,
) -> dict[str, float]:
    """Fit (mu, alpha, beta) to sorted event times in seconds starting at 0.

    Returns mu, alpha, beta, branching ratio, mean intensity, log-likelihood
    and the number of events.
    """
    events = np.asarray(events, dtype=float)
    if len(events) < 50:
        raise ValueError("need at least 50 events to calibrate a Hawkes process")
    events = events - events[0]
    T = float(T if T is not None else events[-1] + 1e-9)
    dts = np.diff(events, prepend=0.0).tolist()
    tail = T - events
    rate = len(events) / T
    rng = np.random.default_rng(seed)
    best = None
    for i in range(n_starts):
        beta0 = [1.0, 5.0, 20.0, 0.3][i % 4] * rng.uniform(0.7, 1.3)
        n0 = rng.uniform(0.3, 0.8)
        theta0 = np.array([math.log(rate * (1 - n0)), math.log(n0 / (1 - n0)), math.log(beta0)])
        res = minimize(_neg_loglik, theta0, args=(dts, tail, T), method="L-BFGS-B",
                       bounds=[(-12, 8), (-8, 8), (-6, 8)])
        if best is None or res.fun < best.fun:
            best = res
    mu = math.exp(best.x[0])
    beta = math.exp(best.x[2])
    n = 1.0 / (1.0 + math.exp(-best.x[1]))
    return {
        "mu": mu,
        "alpha": n * beta,
        "beta": beta,
        "branching_ratio": n,
        "mean_intensity": mu / (1.0 - n),
        "empirical_rate": rate,
        "log_likelihood": -float(best.fun),
        "events": int(len(events)),
        "window_s": T,
    }
