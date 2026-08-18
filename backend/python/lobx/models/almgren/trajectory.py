"""
Almgren-Chriss Optimal Liquidation Trajectory.

Computes the optimal trading trajectory to liquidate X shares over T periods
minimising expected cost + risk.

Reference: Almgren & Chriss (2000), "Optimal Execution of Portfolio Transactions"
"""
from __future__ import annotations
import math
import numpy as np
from numpy.typing import NDArray


def optimal_trajectory(
    total_shares: float,
    T: int,
    sigma: float,
    eta: float,
    gamma: float,
    lambda_: float,
) -> NDArray[np.float64]:
    """
    Compute the optimal SELL trajectory (Almgren-Chriss).

    Parameters
    ----------
    total_shares : X — total quantity to liquidate
    T            : number of periods
    sigma        : volatility per period
    eta          : temporary market impact coefficient
    gamma        : permanent market impact coefficient
    lambda_      : risk-aversion coefficient

    Returns
    -------
    trajectory : array of length T+1 — inventory at each period boundary
                 trajectory[0] = X, trajectory[T] ≈ 0
    """
    kappa_sq = (lambda_ * sigma ** 2) / eta
    kappa    = math.sqrt(kappa_sq)

    t_vals = np.arange(T + 1, dtype=float)
    # Almgren-Chriss closed-form inventory path
    trajectory = total_shares * np.sinh(kappa * (T - t_vals)) / np.sinh(kappa * T)
    return trajectory


def trade_list(trajectory: NDArray[np.float64]) -> NDArray[np.float64]:
    """Convert inventory path to per-period trade quantities (positive = sell)."""
    return -np.diff(trajectory)
