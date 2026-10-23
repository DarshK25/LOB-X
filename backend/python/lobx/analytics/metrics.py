"""
analytics/metrics.py — Strategy evaluation metrics.

Computes standard quantitative metrics for strategy evaluation.
"""
from __future__ import annotations

import math
from collections.abc import Sequence


SECONDS_PER_YEAR = 365.25 * 24.0 * 60.0 * 60.0


def _finite_values(values: Sequence[float]) -> list[float]:
    """Return finite observations without changing their order."""
    return [float(value) for value in values if math.isfinite(float(value))]


def _periods_per_year(
    *,
    annualization_factor: float | None,
    periods_per_year: float | None,
    period_seconds: float | None,
    annualization_seconds: float,
) -> float:
    """Resolve the explicit observation-period/annualization contract.

    ``annualization_factor`` is retained as a backwards-compatible name for
    the number of observations in one year (the value is square-rooted by the
    ratio).  New callers should prefer ``periods_per_year`` or
    ``period_seconds``.  Exactly one of those meanings may be supplied.
    """
    supplied = sum(value is not None for value in (annualization_factor, periods_per_year, period_seconds))
    if supplied > 1:
        raise ValueError(
            "provide only one of annualization_factor, periods_per_year, or period_seconds"
        )
    if period_seconds is not None:
        if period_seconds <= 0.0 or annualization_seconds <= 0.0:
            raise ValueError("period_seconds and annualization_seconds must be positive")
        result = annualization_seconds / period_seconds
    elif periods_per_year is not None:
        result = periods_per_year
    elif annualization_factor is not None:
        result = annualization_factor
    else:
        result = 1.0
    if result <= 0.0 or not math.isfinite(result):
        raise ValueError("annualization must be a finite positive number")
    return float(result)


def _period_risk_free_rate(
    risk_free_rate: float,
    risk_free_rate_annual: float | None,
    periods_per_year: float,
) -> float:
    """Resolve a per-observation risk-free rate.

    The helper's primary contract is that ``risk_free_rate`` is per
    observation.  An annual rate is accepted explicitly and converted using
    compounding so callers do not accidentally subtract an annual rate from a
    one-second return.
    """
    if risk_free_rate_annual is not None:
        if risk_free_rate != 0.0:
            raise ValueError("provide either risk_free_rate or risk_free_rate_annual")
        if risk_free_rate_annual <= -1.0:
            raise ValueError("risk_free_rate_annual must be greater than -1")
        return (1.0 + float(risk_free_rate_annual)) ** (1.0 / periods_per_year) - 1.0
    return float(risk_free_rate)


def compute_sharpe_ratio(
    returns: Sequence[float],
    risk_free_rate: float = 0.0,
    annualization_factor: float | None = None,
    *,
    periods_per_year: float | None = None,
    period_seconds: float | None = None,
    annualization_seconds: float = SECONDS_PER_YEAR,
    risk_free_rate_annual: float | None = None,
) -> float:
    """Compute a sample Sharpe ratio with an explicit period contract.

    Parameters
    ----------
    returns:
        Return or P&L observations.  All observations must use the same
        period; non-finite observations are ignored.
    risk_free_rate:
        Risk-free return *per observation*.  Use ``risk_free_rate_annual`` for
        an annual rate instead.
    periods_per_year:
        Number of observations in the reporting year.  ``period_seconds`` is
        a convenient alternative for regularly sampled observations.
    annualization_factor:
        Backwards-compatible alias for ``periods_per_year``.

    The result is ``mean(excess) / sample_std(excess)`` multiplied by the
    square root of the resolved periods per year.  A ratio is zero when fewer
    than two observations or no variation is available.
    """
    values = _finite_values(returns)
    if len(values) < 2:
        return 0.0

    periods = _periods_per_year(
        annualization_factor=annualization_factor,
        periods_per_year=periods_per_year,
        period_seconds=period_seconds,
        annualization_seconds=annualization_seconds,
    )
    period_rf = _period_risk_free_rate(risk_free_rate, risk_free_rate_annual, periods)
    mean_return = sum(values) / len(values)
    excess_return = mean_return - period_rf

    variance = sum((value - mean_return) ** 2 for value in values) / (len(values) - 1)
    if variance <= 0:
        return 0.0

    std_dev = math.sqrt(variance)
    return (excess_return / std_dev) * math.sqrt(periods)


def compute_sortino_ratio(
    returns: Sequence[float],
    target_return: float = 0.0,
    annualization_factor: float | None = None,
    *,
    periods_per_year: float | None = None,
    period_seconds: float | None = None,
    annualization_seconds: float = SECONDS_PER_YEAR,
    target_return_annual: float | None = None,
) -> float:
    """Compute a downside-deviation Sortino ratio.

    ``target_return`` is the target return per observation, matching the
    period of ``returns``.  Downside deviation is the square root of the mean
    squared negative deviation over *all* observations (zero deviations are
    included), which is the standard target-downside convention.  Annualizing
    uses the same explicit ``periods_per_year``/``period_seconds`` contract as
    :func:`compute_sharpe_ratio`.
    """
    values = _finite_values(returns)
    if not values:
        return 0.0

    periods = _periods_per_year(
        annualization_factor=annualization_factor,
        periods_per_year=periods_per_year,
        period_seconds=period_seconds,
        annualization_seconds=annualization_seconds,
    )
    if target_return_annual is not None:
        if target_return != 0.0:
            raise ValueError("provide either target_return or target_return_annual")
        if target_return_annual <= -1.0:
            raise ValueError("target_return_annual must be greater than -1")
        target_return = (1.0 + float(target_return_annual)) ** (1.0 / periods) - 1.0

    excess = [value - float(target_return) for value in values]
    downside_deviation = math.sqrt(
        sum(min(value, 0.0) ** 2 for value in excess) / len(excess)
    )
    if downside_deviation <= 0.0:
        return 0.0
    return (sum(excess) / len(excess)) / downside_deviation * math.sqrt(periods)


def compute_risk_adjusted_metrics(
    returns: Sequence[float],
    *,
    periods_per_year: float | None = None,
    period_seconds: float | None = None,
    risk_free_rate: float = 0.0,
    risk_free_rate_annual: float | None = None,
    target_return: float = 0.0,
    target_return_annual: float | None = None,
) -> dict[str, float | int]:
    """Return consistent Sharpe/Sortino values and their period metadata."""
    periods = _periods_per_year(
        annualization_factor=None,
        periods_per_year=periods_per_year,
        period_seconds=period_seconds,
        annualization_seconds=SECONDS_PER_YEAR,
    )
    values = _finite_values(returns)
    return {
        "observations": len(values),
        "periods_per_year": periods,
        "risk_free_rate_per_period": (
            _period_risk_free_rate(risk_free_rate, risk_free_rate_annual, periods)
        ),
        "sharpe_ratio": compute_sharpe_ratio(
            values,
            risk_free_rate=risk_free_rate,
            periods_per_year=periods,
            risk_free_rate_annual=risk_free_rate_annual,
        ),
        "sortino_ratio": compute_sortino_ratio(
            values,
            target_return=target_return,
            periods_per_year=periods,
            target_return_annual=target_return_annual,
        ),
    }


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
