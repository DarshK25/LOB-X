"""
Avellaneda-Stoikov reservation price.

r(s, q, tau) = s - q * gamma * sigma^2 * tau

Units: s in USD, q in lots (position / order size), gamma in 1/USD,
sigma in USD/sqrt(second), tau in seconds. Then q*gamma*sigma^2*tau is USD.
"""
from __future__ import annotations


def reservation_price(
    mid: float,
    inventory: float,
    gamma: float,
    sigma: float,
    time_remaining: float,
) -> float:
    """Inventory-adjusted indifference price. Long inventory lowers it."""
    if gamma <= 0:
        raise ValueError("gamma must be positive")
    return mid - inventory * gamma * (sigma ** 2) * time_remaining
