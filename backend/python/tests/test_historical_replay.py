"""Regression tests for deterministic, no-lookahead historical replay."""
from __future__ import annotations

import pandas as pd
import pytest

from lobx.backtest.replay import (
    DATA_SOURCE_SYNTHETIC_FROM_TRADES,
    BacktestConfig,
    HistoricalReplay,
)
from lobx.execution.shadow_engine import ExecutionAssumptions
from lobx.risk import MarketMakingRiskConfig
from lobx.strategy.avellaneda import AvellanedaStoikovStrategy
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
    assert result.data_source == "real_l2"
    assert result.summary["data_source"] == "real_l2"
    assert result.execution_counters["shadow"]["fills"] == 1
    assert result.execution_counters["shadow"]["filled_qty"] == pytest.approx(0.001)
    assert result.execution_counters["shadow"]["quotes_placed"] > 0
    assert result.summary["fills"] == 2  # shadow buy plus forced flatten
    assert result.summary["final_position"] == pytest.approx(0.0)
    assert result.summary["gross_pnl"] == pytest.approx(0.001)
    # The reference mid is 100.50, so a buy fill at 99.00 has a +1.50 USDT
    # per BTC one-second markout; the fill size is 0.001 BTC.
    assert result.summary["mean_1s_markout"] == pytest.approx(0.0015)
    assert result.summary["mean_100ms_markout"] == pytest.approx(0.0015)
    assert result.summary["mean_5000ms_markout"] is None
    assert result.summary["markout_observations"] == 3
    assert len(result.markouts) == 6
    assert sum(record["available"] for record in result.markouts) == 3
    assert sum(not record["available"] for record in result.markouts) == 3
    assert any(event["event_type"] == "TRADE_RECEIVED" for event in result.events)
    assert any(event["event_type"] == "FILL" for event in result.events)
    assert any(event["event_type"] == "QUOTE_CALCULATED" for event in result.events)


def test_replay_summary_includes_maker_taker_fee_accounting():
    depth, trades = _capture()
    result = HistoricalReplay(
        _config(
            execution=ExecutionAssumptions(maker_fee_rate=0.0001),
            taker_fee_rate=0.0005,
        )
    ).run(
        depth, trades, lambda: FixedSpreadStrategy(half_spread=1.5, qty=0.001)
    )

    assert result.summary["maker_fees"] == pytest.approx(99.0 * 0.001 * 0.0001)
    assert result.summary["taker_fees"] == pytest.approx(100.0 * 0.001 * 0.0005)
    assert result.summary["fees"] == pytest.approx(0.0000599)
    assert result.summary["traded_notional"] == pytest.approx(0.199)
    assert result.summary["net_pnl"] == pytest.approx(0.001 - 0.0000599)
    assert result.summary["reconciliation_residual"] == pytest.approx(0.0)


def test_latency_prevents_same_window_fill():
    depth, trades = _capture()
    config = _config(execution=ExecutionAssumptions(latency_ms=200))
    result = HistoricalReplay(config).run(
        depth, trades, lambda: FixedSpreadStrategy(half_spread=1.5, qty=0.001)
    )
    assert result.summary["fills"] == 0
    assert result.summary["net_pnl"] == pytest.approx(0.0)


def test_replay_forwards_absolute_depth_updates_to_conservative_queue():
    depth, trades = _capture()
    trades = trades.iloc[:0].copy()
    depth = pd.concat(
        [
            depth,
            pd.DataFrame(
                [(1_050, "bid", 100.00, 0.5)],
                columns=["event_time", "side", "price", "qty"],
            ),
        ],
        ignore_index=True,
    )
    result = HistoricalReplay(
        _config(execution=ExecutionAssumptions(queue_model="conservative"))
    ).run(
        depth,
        trades,
        lambda: FixedSpreadStrategy(half_spread=0.5, qty=0.001),
    )

    queue_events = [
        event
        for event in result.events
        if event["event_type"] == "QUEUE_UPDATED"
        and event["reason"] == "displayed_depth_update"
        and event["depth_change"] < 0
    ]
    assert queue_events
    assert queue_events[-1]["previous_displayed_depth_qty"] == pytest.approx(1.0)
    assert queue_events[-1]["displayed_depth_qty"] == pytest.approx(0.5)
    assert result.summary["maker_fill_count"] == 0


def test_unavailable_markouts_are_explicitly_recorded():
    depth, trades = _capture()
    result = HistoricalReplay(_config()).run(
        depth.iloc[:100],
        trades,
        lambda: FixedSpreadStrategy(half_spread=1.5, qty=0.001),
    )
    assert result.summary["markout_observations"] == 0
    assert len(result.markouts) == 6
    assert all(not record["available"] for record in result.markouts)
    assert sum(event["event_type"] == "MARKOUT_UNAVAILABLE" for event in result.events) == 6


def test_replay_rejects_depth_diffs_without_snapshot():
    depth, trades = _capture()
    invalid_depth = depth.iloc[100:].copy()
    with pytest.raises(ValueError, match="initial depth snapshot"):
        HistoricalReplay(_config()).run(
            invalid_depth, trades, lambda: FixedSpreadStrategy(half_spread=1.5, qty=0.001)
        )


def test_synthetic_depth_provenance_warns_for_conservative_queue_mode():
    _, trades = _capture()
    replay = HistoricalReplay.from_binance_dataframe(
        trades,
        config=_config(execution=ExecutionAssumptions(queue_model="conservative")),
    )
    assert replay.data_source == DATA_SOURCE_SYNTHETIC_FROM_TRADES

    with pytest.warns(UserWarning, match="synthetic_from_trades"):
        result = replay.run(
            lambda: FixedSpreadStrategy(half_spread=0.5, qty=0.001),
        )

    assert result.data_source == DATA_SOURCE_SYNTHETIC_FROM_TRADES
    assert result.warnings
    assert result.execution_counters["conservative"]["depth_advance_events"] >= 0


def test_fill_updates_inventory_before_next_avellaneda_quote():
    """A replay fill must be visible to the next AS reservation quote."""
    depth_rows = [
        (1_000, "bid", 100.00 - index * 0.01, 1.0)
        for index in range(50)
    ] + [
        (1_000, "ask", 101.00 + index * 0.01, 1.0)
        for index in range(50)
    ]
    # Move the top of book after the initial quote. Clearing the old top first
    # keeps the reference book valid while also giving realized volatility a
    # timestamped return before the post-fill quote.
    depth_rows.extend([
        (1_050, "ask", 101.00, 0.0),
        (1_050, "bid", 100.00, 0.0),
        (1_050, "bid", 101.00, 1.0),
        (1_050, "ask", 102.00, 1.0),
        (1_200, "bid", 101.00, 1.0),
    ])
    depth = pd.DataFrame(depth_rows, columns=["event_time", "side", "price", "qty"])
    trades = pd.DataFrame(
        [(1_100, 1, 99.00, 0.10, True, "BTCUSDT")],
        columns=["event_time", "trade_id", "price", "qty", "is_buyer_maker", "symbol"],
    )
    config = _config(
        execution=ExecutionAssumptions(latency_ms=0),
        risk=MarketMakingRiskConfig(max_position=1.0, max_order_qty=0.1, max_drawdown=100.0),
    )

    result = HistoricalReplay(config).run(
        depth,
        trades,
        lambda: AvellanedaStoikovStrategy(
            gamma=2.0,
            kappa=1.5,
            horizon_seconds=300.0,
            qty=0.1,
            min_spread=0.02,
        ),
    )

    maker_fills = [fill for fill in result.fills if fill["order_id"] != "forced_flatten"]
    quotes = [event for event in result.events if event["event_type"] == "QUOTE_CALCULATED"]
    assert len(maker_fills) == 1
    assert maker_fills[0]["side"] == "buy"
    assert maker_fills[0]["qty"] == pytest.approx(0.1)
    assert len(quotes) >= 2
    assert quotes[0]["inventory"] == pytest.approx(0.0)
    assert quotes[1]["inventory"] == pytest.approx(0.1)
    assert quotes[1]["details"]["inventory_skew"] < 0.0
    assert quotes[1]["details"]["sigma"] > 0.0

    # Independent reservation-price oracle at the post-fill quote.  The USD
    # shift is q * gamma * sigma_usd^2 * tau, not a shift in log-vol units.
    post_fill = next(
        event for event in quotes if event["inventory"] == pytest.approx(0.1)
    )
    details = post_fill["details"]
    expected_reservation = (
        post_fill["market_mid"]
        - post_fill["inventory"]
        * 2.0
        * details["sigma_usd"] ** 2
        * details["time_remaining"]
    )
    assert details["reservation_price"] == pytest.approx(expected_reservation)
    assert post_fill["inventory"] == pytest.approx(0.1)
    assert post_fill["market_mid"] - details["reservation_price"] == pytest.approx(
        0.1 * 2.0 * details["sigma_usd"] ** 2 * details["time_remaining"]
    )
    assert details["sigma_usd"] == pytest.approx(
        details["sigma_log"] * post_fill["market_mid"]
    )
    assert details["reservation_price"] < post_fill["market_mid"]
