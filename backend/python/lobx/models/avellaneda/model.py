"""Canonical Avellaneda-Stoikov quote calculation.

Convention used by every caller:
    time_remaining is seconds and sigma is price units per sqrt(second).
"""
from __future__ import annotations

import math
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


def to_usd_sigma(sigma_log: float, mid: float) -> float:
    """Convert log-price volatility to USD price volatility.

    ``sigma_log`` is expressed in log-return per sqrt(second), while the
    canonical AS equations consume USD per sqrt(second).  Keeping this
    conversion at the model boundary prevents a calibration floor and a
    rolling estimate from being compared in different units.
    """
    sigma_log = float(sigma_log)
    mid = float(mid)
    if not math.isfinite(sigma_log) or sigma_log < 0.0:
        raise ValueError("sigma_log must be finite and non-negative")
    if not math.isfinite(mid) or mid <= 0.0:
        raise ValueError("mid must be finite and positive")
    return sigma_log * mid


def gamma_for_target_skew(
    target_ticks: float,
    inventory_limit: float,
    sigma_usd: float,
    tau: float,
    tick: float,
) -> float:
    """Return gamma that produces ``target_ticks`` of reservation skew.

    The target is the absolute reservation-price shift at the inventory limit:
    ``|q * gamma * sigma_usd**2 * tau|``.  This helper is a design tool, not a
    calibration estimator.
    """
    values = (target_ticks, inventory_limit, sigma_usd, tau, tick)
    if any(not math.isfinite(float(value)) for value in values):
        raise ValueError("target skew inputs must be finite")
    if target_ticks < 0 or inventory_limit <= 0 or sigma_usd <= 0 or tau <= 0 or tick <= 0:
        raise ValueError("target_ticks must be non-negative and remaining inputs positive")
    return target_ticks * tick / (inventory_limit * sigma_usd**2 * tau)


def calculate_quote(
    mid: float,
    inventory: float,
    gamma: float,
    sigma: float,
    time_remaining: float,
    kappa: float,
) -> ASQuote:
    """Calculate reservation price and symmetric quotes around it."""
    reservation = reservation_price(
        mid, inventory, gamma, sigma, time_remaining
    )
    spread_half = half_spread(
        gamma, sigma, time_remaining, kappa
    )
    return ASQuote(
        reservation_price=reservation,
        full_spread=spread_half * 2.0,
        bid=reservation - spread_half,
        ask=reservation + spread_half,
    )
