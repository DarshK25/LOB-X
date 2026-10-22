"""Guéant-Lehalle-Fernandez-Tapia (GLFT) optimal market-making quotes.

Reference: O. Guéant, C.-A. Lehalle, J. Fernandez-Tapia, "Dealing with the
inventory risk: a solution to the market making problem", Mathematics and
Financial Economics 7 (2013).  The closed form below is the asymptotic
(infinite-horizon) approximation, which suits a 24/7 market better than the
finite-horizon Avellaneda-Stoikov solution whose skew collapses as t -> T.

Units (all callers must follow these):
    sigma : USD per sqrt(second)      mid-price diffusion coefficient
    A     : 1 / second                market-order arrival rate reaching the mid
    k     : 1 / USD                   decay of arrival rate with quote depth,
                                      lambda(delta) = A * exp(-k * delta)
    gamma : 1 / USD                   risk aversion per lot of inventory
    delta : lots per order (1 = one order size)
    q     : inventory in lots (position / order size)

Quotes:
    c1 = 1/(gamma*delta) * ln(1 + gamma*delta/k)
    c2 = sqrt( gamma / (2*A*delta*k) * (1 + gamma*delta/k)^(k/(gamma*delta) + 1) )
    half_spread = c1 + delta/2 * sigma * c2
    skew        = sigma * c2
    bid_depth   = half_spread + skew * q      (long  -> bid moves away)
    ask_depth   = half_spread - skew * q      (long  -> ask moves closer)
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class GLFTCoefficients:
    c1: float  # USD
    c2: float  # sqrt(second)


def glft_coefficients(gamma: float, k: float, A: float, delta: float = 1.0) -> GLFTCoefficients:
    if gamma <= 0 or k <= 0 or A <= 0 or delta <= 0:
        raise ValueError("gamma, k, A and delta must be positive")
    x = gamma * delta / k
    c1 = math.log1p(x) / (gamma * delta)
    # (1+x)^(1/x + 1) evaluated in log space; stable for small x.
    growth = math.exp((1.0 / x + 1.0) * math.log1p(x))
    c2 = math.sqrt(gamma / (2.0 * A * delta * k) * growth)
    return GLFTCoefficients(c1=c1, c2=c2)


def glft_depths(sigma: float, q: float, coeffs: GLFTCoefficients,
                delta: float = 1.0) -> tuple[float, float, float, float]:
    """Return (bid_depth, ask_depth, half_spread, skew) in USD from the mid."""
    half_spread = coeffs.c1 + 0.5 * delta * sigma * coeffs.c2
    skew = sigma * coeffs.c2
    return half_spread + skew * q, half_spread - skew * q, half_spread, skew
