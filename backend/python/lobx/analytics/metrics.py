"""
analytics/metrics.py — Strategy evaluation metrics.

Computes standard quantitative metrics for strategy evaluation.
"""
from __future__ import annotations

import math
from typing import Sequence


def compute_sharpe_ratio(returns: Sequence[float], risk_free_rate: float = 0.0, annualization_factor: float = 1.0) -> float:
    """Compute the Sharpe ratio from a sequence of returns.
    
    Sharpe = (E[R] - Rf) / Std[R]
    """
    if len(returns) < 2:
        return 0.0
        
    mean_return = sum(returns) / len(returns)
    excess_return = mean_return - risk_free_rate
    
    variance = sum((r - mean_return) ** 2 for r in returns) / (len(returns) - 1)
    if variance <= 0:
        return 0.0
        
    std_dev = math.sqrt(variance)
    return (excess_return / std_dev) * math.sqrt(annualization_factor)


def compute_max_drawdown(equity_curve: Sequence[float]) -> float:
    """Compute the maximum drawdown from an equity curve (cumulative PnL or balance).
    
    Returns
    -------
    float: Maximum peak-to-trough drop as a positive percentage (e.g. 0.05 for 5% drop).
           Returns 0.0 if the curve never drops.
    """
    if not equity_curve:
        return 0.0
        
    max_peak = equity_curve[0]
    max_dd = 0.0
    
    for value in equity_curve:
        if value > max_peak:
            max_peak = value
        
        if max_peak > 0:
            dd = (max_peak - value) / max_peak
            if dd > max_dd:
                max_dd = dd
                
    return max_dd


def compute_inventory_drift(inventory_series: Sequence[float]) -> float:
    """Compute the average inventory drift from 0.
    
    A market making strategy ideally maintains inventory near 0.
    This metric computes the mean absolute inventory.
    """
    if not inventory_series:
        return 0.0
        
    return sum(abs(i) for i in inventory_series) / len(inventory_series)
