"""
Avellaneda-Stoikov Reservation Price.

r(s, q, t) = s - q * γ * σ² * (T - t)

Where:
    s   = current mid-price
    q   = current inventory (signed)
    γ   = risk-aversion parameter
    σ   = volatility (std of mid-price)
    T-t = remaining time horizon
"""
from __future__ import annotations
import math


def reservation_price(
    mid: float,
    inventory: float,
    gamma: float,
    sigma: float,
    time_remaining: float,
) -> float:
    """
    Compute the AS reservation price.

    Parameters
    ----------
    mid           : current mid-price
    inventory     : signed inventory (positive = long)
    gamma         : absolute risk aversion coefficient (> 0)
    sigma         : annualised or per-tick volatility
    time_remaining: fraction of trading session remaining [0, 1]

    Returns
    -------
    Reservation price r
    """
    if gamma <= 0:
        raise ValueError("gamma must be positive")
    return mid - inventory * gamma * (sigma ** 2) * time_remaining
