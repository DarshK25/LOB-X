"""Regression tests for deterministic, no-lookahead historical replay."""
from __future__ import annotations

import pandas as pd
import pytest

from lobx.backtest.replay import BacktestConfig, HistoricalReplay
from lobx.execution.shadow_engine import ExecutionAssumptions
from lobx.risk import MarketMakingRiskConfig
from lobx.strategy.fixed_spread import FixedSpreadStrategy


def _capture() -> tuple[pd.DataFrame, pd.DataFrame]:
    # The first timestamp emulates the collector's persisted 100-level
    # bootstrap snapshot. Later events are ordinary incremental diffs.
    depth_rows = []
    for index in range(50):
        depth_rows.append((1_000, "bid", 100.00 - index * 0.01, 1.0))
        depth_rows.append((1_000, "ask", 101.00 + index * 0.01, 1.0))
    depth_rows.append((2_100, "bid", 100.00, 1.0))
    depth = pd.DataFrame(depth_rows, columns=["event_time", "side", "price", "qty"])
    trades = pd.DataFrame(
        [(1_100, 1, 99.0, 0.001, True, "BTCUSDT")],
        columns=["event_time", "trade_id", "price", "qty", "is_buyer_maker", "symbol"],
    )
    return depth, trades


def _config(**overrides) -> BacktestConfig:
    defaults = dict(
        name="test",
        quote_interval_ms=100,
        markout_horizon_ms=1_000,
        taker_fee_rate=0.0,
        execution=ExecutionAssumptions(latency_ms=0),
        risk=MarketMakingRiskConfig(max_position=0.01, max_order_qty=0.001, max_drawdown=100.0),
    )
    defaults.update(overrides)
    return BacktestConfig(**defaults)


def test_replay_fills_then_flattens_and_reports_pnl():
    depth, trades = _capture()
    result = HistoricalReplay(_config()).run(
        depth, trades, lambda: FixedSpreadStrategy(half_spread=1.5, qty=0.001)
    )

    assert result.depth_events == 2
    assert result.trade_events == 1
    assert result.summary["fills"] == 2  # shadow buy plus forced flatten
    assert result.summary["final_position"] == pytest.approx(0.0)
    assert result.summary["gross_pnl"] == pytest.approx(0.001)
    # The reference mid is 100.50, so a buy fill at 99.00 has a +1.50 USDT
    # per BTC one-second markout; the fill size is 0.001 BTC.
    assert result.summary["mean_1s_markout"] == pytest.approx(0.0015)


def test_latency_prevents_same_window_fill():
    depth, trades = _capture()
    config = _config(execution=ExecutionAssumptions(latency_ms=200))
    result = HistoricalReplay(config).run(
        depth, trades, lambda: FixedSpreadStrategy(half_spread=1.5, qty=0.001)
    )
    assert result.summary["fills"] == 0
    assert result.summary["net_pnl"] == pytest.approx(0.0)


def test_replay_rejects_depth_diffs_without_snapshot():
    depth, trades = _capture()
    invalid_depth = depth.iloc[100:].copy()
    with pytest.raises(ValueError, match="initial depth snapshot"):
        HistoricalReplay(_config()).run(
            invalid_depth, trades, lambda: FixedSpreadStrategy(half_spread=1.5, qty=0.001)
        )
