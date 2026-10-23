"""
Avellaneda-Stoikov Optimal Spread.

δ* = γ * σ² * (T - t) + (2/γ) * ln(1 + γ/κ)

Where:
    γ   = risk aversion
    σ   = volatility
    T-t = time remaining
    κ   = order arrival intensity parameter
"""
from __future__ import annotations
import math


def spread_decomposition(
    gamma: float,
    sigma: float,
    time_remaining: float,
    kappa: float,
) -> dict[str, float]:
    """Return the AS spread's variance, kappa, and total components.

    The returned ``sigma`` is the canonical model input: USD per
    sqrt(second).  ``time_remaining`` is seconds, so both terms are USD.
    """
    if kappa <= 0:
        raise ValueError("kappa must be positive")
    if gamma <= 0:
        raise ValueError("gamma must be positive")

    variance_term = gamma * (sigma ** 2) * time_remaining
    kappa_term = (2.0 / gamma) * math.log(1.0 + gamma / kappa)
    return {
        "variance_term": variance_term,
        "kappa_term": kappa_term,
        "spread_total": variance_term + kappa_term,
    }


def optimal_spread(
    gamma: float,
    sigma: float,
    time_remaining: float,
    kappa: float,
) -> float:
    """
    Compute the full AS optimal half-spread.

    Parameters
    ----------
    gamma         : risk-aversion coefficient
    sigma         : volatility
    time_remaining: time remaining in session [0, 1]
    kappa         : order flow intensity

    Returns
    -------
    Full optimal spread δ*
    """
    return spread_decomposition(gamma, sigma, time_remaining, kappa)["spread_total"]


def half_spread(gamma: float, sigma: float, time_remaining: float, kappa: float) -> float:
    """Optimal half-spread (distance from reservation to each quote)."""
    return optimal_spread(gamma, sigma, time_remaining, kappa) / 2.0
