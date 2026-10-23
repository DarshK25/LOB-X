"""Focused tests for evaluation metrics and chronological validation."""
from __future__ import annotations

import pandas as pd
import pytest

from lobx.analytics.metrics import (
    SECONDS_PER_YEAR,
    compute_sharpe_ratio,
    compute_sortino_ratio,
)
from lobx.analytics.pnl_waterfall import summarize_pnl_waterfall
from lobx.analytics.walk_forward import (
    WalkForwardConfig,
    chronological_split,
    fit_train_parameters,
    run_walk_forward,
)


def _trades(count: int = 12) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "event_time": [1_000 + index * 1_000 for index in range(count)],
            "trade_id": list(range(count)),
            "price": [100.0 + (index % 3) * 0.10 for index in range(count)],
            "qty": [0.001] * count,
            "is_buyer_maker": [index % 2 == 0 for index in range(count)],
            "symbol": ["BTCUSDT"] * count,
        }
    )


def test_sharpe_and_sortino_use_explicit_period_semantics():
    returns = [1.0, 2.0, 3.0]
    assert compute_sharpe_ratio(returns, periods_per_year=4.0) == pytest.approx(4.0)
    assert compute_sharpe_ratio(returns, period_seconds=SECONDS_PER_YEAR / 4.0) == pytest.approx(4.0)

    # Mean excess is 0.5; downside deviation is sqrt((1^2 + 1^2) / 4),
    # then annualized by sqrt(4).  Downside deviation includes zero/positive
    # observations in its denominator, which is the target-downside convention.
    assert compute_sortino_ratio([2.0, -1.0, 2.0, -1.0], periods_per_year=4.0) == pytest.approx(1.4142135624)


def test_pnl_waterfall_filters_markout_horizon_and_reconciles():
    result = summarize_pnl_waterfall(
        fills=[{"execution_edge": 0.1}],
        markouts=[
            {"available": True, "horizon_ms": 100, "markout": -0.2},
            {"available": True, "horizon_ms": 1_000, "markout": -0.7},
        ],
        realized_pnl=0.4,
        unrealized_pnl=-0.1,
        fees=0.05,
        markout_horizon_ms=1_000,
    )
    assert result["adverse_selection_markout"] == pytest.approx(-0.7)
    assert result["net_pnl"] == pytest.approx(0.25)
    assert result["pnl_reconciled"] is True
    assert result["pnl_reconciliation_error"] == pytest.approx(0.0)


def test_split_keeps_equal_timestamp_events_together():
    trades = _trades()
    trades.loc[5:6, "event_time"] = 6_000
    train, test = chronological_split(trades, train_fraction=0.5)
    assert train.event_time.max() < test.event_time.min()
    assert set(train.trade_id).isdisjoint(test.trade_id)


def test_walk_forward_fits_train_and_scores_three_oos_benchmarks():
    trades = _trades()
    train, _ = chronological_split(trades, train_fraction=0.5)
    fit = fit_train_parameters(train, inventory_limit=0.005)
    result = run_walk_forward(
        trades,
        config=WalkForwardConfig(
            train_fraction=0.5,
            quote_interval_ms=100,
            qty=0.001,
            inventory_limit=0.005,
            fixed_half_spread=0.5,
            spread_floor=0.02,
            horizon=10.0,
            taker_fee_rate=0.0,
        ),
    )
    assert result.calibration["fit_rows"] == len(train)
    assert result.calibration["fit_end"] < result.test_start
    assert set(result.results) == {"fixed", "as", "no_trade"}
    assert result.results["no_trade"].summary["net_pnl"] == pytest.approx(0.0)
    assert all("sharpe_ratio" in item.summary for item in result.results.values())
    assert all(item.summary["pnl_reconciled"] is True for item in result.results.values())
    as_quotes = [
        event for event in result.results["as"].events
        if event["event_type"] == "QUOTE_CALCULATED"
    ]
    assert as_quotes[0]["details"]["time_remaining"] == pytest.approx(10.0)
