"""
Avellaneda-Stoikov optimal total spread.

delta = gamma * sigma^2 * tau + (2/gamma) * ln(1 + gamma/k)

Units: gamma and k in 1/USD, sigma in USD/sqrt(second), tau in seconds.
Both terms are then in USD. With k in 1/USD the liquidity term is a real price
distance; if k is left at a textbook value such as 1.5 it is not.
"""
from __future__ import annotations

import math


def optimal_spread(gamma: float, sigma: float, time_remaining: float, kappa: float) -> float:
    """Full bid-ask spread (USD) around the reservation price."""
    if kappa <= 0:
        raise ValueError("kappa must be positive")
    if gamma <= 0:
        raise ValueError("gamma must be positive")
    risk_term = gamma * (sigma ** 2) * time_remaining
    liquidity_term = (2.0 / gamma) * math.log1p(gamma / kappa)
    return risk_term + liquidity_term


def half_spread(gamma: float, sigma: float, time_remaining: float, kappa: float) -> float:
    """Distance from the reservation price to each quote (USD)."""
    return optimal_spread(gamma, sigma, time_remaining, kappa) / 2.0
