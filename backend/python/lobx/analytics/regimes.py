"""Market-regime attribution for historical paper fills.

This module is diagnostic, not predictive. It labels the market state known at
each fill and measures the subsequent mid-price markout, helping distinguish
spread capture from adverse selection before a parameter search begins.
"""
from __future__ import annotations

from dataclasses import dataclass
import math

import pandas as pd

from lobx.market_data.depth_stream_manager import LocalOrderBook


@dataclass(frozen=True)
class RegimeConfig:
    sample_interval_ms: int = 1_000
    volatility_window: int = 60
    markout_horizon_ms: int = 1_000

    def __post_init__(self) -> None:
        if self.sample_interval_ms <= 0 or self.volatility_window < 2 or self.markout_horizon_ms < 0:
            raise ValueError("invalid regime-analysis intervals")


def sample_market_states(depth: pd.DataFrame, config: RegimeConfig | None = None) -> pd.DataFrame:
    """Reconstruct and sample a valid L2 book without using future updates."""
    config = config or RegimeConfig()
    required = {"event_time", "side", "price", "qty"}
    if not required.issubset(depth.columns):
        raise ValueError(f"depth data missing columns: {sorted(required - set(depth.columns))}")
    data = depth[["event_time", "side", "price", "qty"]]
    if not data.event_time.is_monotonic_increasing:
        data = data.sort_values(["event_time", "side", "price"], kind="stable")

    book = LocalOrderBook()
    next_sample_at: int | None = None
    samples: list[dict] = []
    for timestamp, group in data.groupby("event_time", sort=False):
        for row in group.itertuples(index=False):
            book.apply_levels(row.side, [(float(row.price), float(row.qty))])
        bid, ask = book.best_bid(), book.best_ask()
        timestamp = int(timestamp)
        if bid is None or ask is None or bid[0] >= ask[0]:
            continue
        if next_sample_at is not None and timestamp < next_sample_at:
            continue
        samples.append(
            {
                "timestamp": timestamp,
                "mid_price": (bid[0] + ask[0]) / 2.0,
                "spread": ask[0] - bid[0],
                "top_liquidity": bid[1] + ask[1],
            }
        )
        next_sample_at = timestamp + config.sample_interval_ms

    states = pd.DataFrame(samples)
    if states.empty:
        return states
    log_returns = states.mid_price.pct_change().map(
        lambda value: None if pd.isna(value) else math.log1p(value)
    )
    states["volatility_bps"] = log_returns.rolling(config.volatility_window, min_periods=config.volatility_window).std() * 10_000
    states["volatility_regime"] = _terciles(states["volatility_bps"])
    states["liquidity_regime"] = _terciles(states["top_liquidity"])
    return states


def attribute_fills_to_regimes(
    fills: pd.DataFrame,
    states: pd.DataFrame,
    config: RegimeConfig | None = None,
) -> pd.DataFrame:
    """Attach pre-fill state and future mid-price markout to each maker fill."""
    config = config or RegimeConfig()
    required = {"order_id", "side", "price", "qty", "timestamp", "fee"}
    if not required.issubset(fills.columns):
        raise ValueError(f"fill data missing columns: {sorted(required - set(fills.columns))}")
    if states.empty:
        return pd.DataFrame()
    maker_fills = fills[fills.order_id.astype(str) != "forced_flatten"].copy()
    maker_fills = maker_fills.sort_values("timestamp")
    maker_fills["signed_qty"] = maker_fills.qty.where(maker_fills.side == "buy", -maker_fills.qty)
    prior = pd.merge_asof(
        maker_fills,
        states.sort_values("timestamp"),
        on="timestamp",
        direction="backward",
    )
    future_query = maker_fills[["timestamp"]].copy()
    future_query["due_at"] = future_query.timestamp + config.markout_horizon_ms
    future_states = states[["timestamp", "mid_price"]].rename(columns={"mid_price": "future_mid"})
    future = pd.merge_asof(
        future_query.sort_values("due_at"),
        future_states.sort_values("timestamp"),
        left_on="due_at",
        right_on="timestamp",
        direction="forward",
    )
    prior["markout_1s"] = prior.signed_qty * (future.future_mid.to_numpy() - prior.price)
    return prior


def summarise_regimes(attributed_fills: pd.DataFrame) -> pd.DataFrame:
    """Aggregate adverse-selection evidence by known volatility/liquidity state."""
    if attributed_fills.empty:
        return pd.DataFrame(
            columns=["volatility_regime", "liquidity_regime", "fills", "filled_qty", "fees", "mean_1s_markout", "total_1s_markout"]
        )
    return (
        attributed_fills.dropna(subset=["volatility_regime", "liquidity_regime"])
        .groupby(["volatility_regime", "liquidity_regime"], observed=True)
        .agg(
            fills=("qty", "size"),
            filled_qty=("qty", "sum"),
            fees=("fee", "sum"),
            mean_1s_markout=("markout_1s", "mean"),
            total_1s_markout=("markout_1s", "sum"),
        )
        .reset_index()
        .sort_values(["volatility_regime", "liquidity_regime"])
    )


def _terciles(values: pd.Series) -> pd.Series:
    valid = values.notna()
    result = pd.Series("unwarmed", index=values.index, dtype="object")
    if valid.sum() < 3:
        return result
    ranked = values[valid].rank(method="first")
    result.loc[valid] = pd.qcut(ranked, q=3, labels=["low", "medium", "high"]).astype(str)
    return result
