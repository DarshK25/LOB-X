"""Canonical Avellaneda-Stoikov quote calculation.

Units: mid USD, inventory in lots, gamma and kappa in 1/USD,
sigma in USD/sqrt(second), time_remaining in seconds.
"""
from __future__ import annotations

from dataclasses import dataclass

from lobx.models.avellaneda.optimal_spread import half_spread
from lobx.models.avellaneda.reservation_price import reservation_price


@dataclass(frozen=True)
class ASQuote:
    """Unrounded AS quote components for audit and display."""

    reservation_price: float
    full_spread: float
    bid: float
    ask: float


def calculate_quote(
    mid: float,
    inventory: float,
    gamma: float,
    sigma: float,
    time_remaining: float,
    kappa: float,
) -> ASQuote:
    """Reservation price and symmetric quotes around it."""
    reservation = reservation_price(mid, inventory, gamma, sigma, time_remaining)
    spread_half = half_spread(gamma, sigma, time_remaining, kappa)
    return ASQuote(
        reservation_price=reservation,
        full_spread=spread_half * 2.0,
        bid=reservation - spread_half,
        ask=reservation + spread_half,
    )
