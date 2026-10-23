"""Chronological train-fit-test evaluation helpers.

This module deliberately keeps the experiment boundary small and explicit:
the train frame is used only to fit the A-S parameters, while every strategy
run uses a fresh replay built from the untouched test frame.  The replay's
existing execution, queue, and risk configuration remains the source of truth.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from typing import Any

import pandas as pd

from lobx.analytics.calibration import calibrate_gamma, calibrate_kappa, calibrate_sigma
from lobx.backtest.replay import BacktestConfig, BacktestResult, HistoricalReplay
from lobx.execution.shadow_engine import ExecutionAssumptions
from lobx.risk import MarketMakingRiskConfig
from lobx.strategy.avellaneda import AvellanedaStoikovStrategy
from lobx.strategy.fixed_spread import FixedSpreadStrategy
from lobx.strategy.no_trade import NoTradeStrategy


@dataclass(frozen=True)
class WalkForwardConfig:
    """Settings that define one reproducible chronological evaluation."""

    train_fraction: float = 0.70
    quote_interval_ms: int = 100
    qty: float = 0.001
    inventory_limit: float = 0.005
    fixed_half_spread: float = 0.50
    spread_floor: float = 0.02
    horizon: float = 300.0
    gamma: float | None = None
    kappa: float | None = None
    maker_fee_rate: float = 0.0
    taker_fee_rate: float = 0.0005
    flatten_at_end: bool = True

    def __post_init__(self) -> None:
        if not 0.0 < self.train_fraction < 1.0:
            raise ValueError("train_fraction must be strictly between 0 and 1")
        if self.quote_interval_ms < 0:
            raise ValueError("quote_interval_ms cannot be negative")
        if self.qty <= 0.0 or self.inventory_limit <= 0.0:
            raise ValueError("qty and inventory_limit must be positive")
        if self.fixed_half_spread < 0.0 or self.spread_floor < 0.0:
            raise ValueError("spreads cannot be negative")
        if not math.isfinite(self.horizon) or self.horizon <= 0.0:
            raise ValueError("horizon must be finite and positive")
        if self.maker_fee_rate < 0.0 or self.taker_fee_rate < 0.0:
            raise ValueError("fee rates cannot be negative")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class WalkForwardResult:
    """Results and provenance for one train-fit-test run."""

    config: dict[str, Any]
    train_rows: int
    test_rows: int
    train_start: int
    train_end: int
    test_start: int
    test_end: int
    calibration: dict[str, Any]
    results: dict[str, BacktestResult]

    @property
    def scoreboard(self) -> dict[str, dict[str, Any]]:
        """Return JSON-ready summaries without exposing mutable replay objects."""
        return {name: dict(result.summary) for name, result in self.results.items()}

    def to_report(self) -> dict[str, Any]:
        return {
            "config": self.config,
            "split": {
                "train_rows": self.train_rows,
                "test_rows": self.test_rows,
                "train_start": self.train_start,
                "train_end": self.train_end,
                "test_start": self.test_start,
                "test_end": self.test_end,
                "chronological": self.train_end <= self.test_start,
            },
            "calibration": self.calibration,
            "scoreboard": self.scoreboard,
        }


def hash_dataframe(frame: pd.DataFrame) -> str:
    """Hash exact frame content and column order for experiment provenance."""
    payload = {
        "columns": [str(column) for column in frame.columns],
        "dtypes": [str(dtype) for dtype in frame.dtypes],
        "rows": len(frame),
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8"))
    digest.update(pd.util.hash_pandas_object(frame, index=True).to_numpy().tobytes())
    return digest.hexdigest()


def _normalise_trades(trades: pd.DataFrame) -> pd.DataFrame:
    frame = trades.copy()
    if "event_time" not in frame.columns and "transact_time" in frame.columns:
        frame = frame.rename(columns={"transact_time": "event_time"})
    if "trade_id" not in frame.columns and "agg_trade_id" in frame.columns:
        frame = frame.rename(columns={"agg_trade_id": "trade_id"})
    required = {"event_time", "price"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"trade data missing columns: {sorted(missing)}")
    if frame.empty:
        raise ValueError("trade data is empty")
    sort_columns = ["event_time"]
    if "trade_id" in frame.columns:
        sort_columns.append("trade_id")
    return frame.sort_values(sort_columns, kind="stable").reset_index(drop=True)


def chronological_split(
    trades: pd.DataFrame,
    *,
    train_fraction: float = 0.70,
    max_trades: int | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Sort once by exchange time and return disjoint train/test frames."""
    if not 0.0 < train_fraction < 1.0:
        raise ValueError("train_fraction must be strictly between 0 and 1")
    frame = _normalise_trades(trades)
    if max_trades is not None:
        if max_trades < 2:
            raise ValueError("max_trades must leave at least two observations")
        frame = frame.iloc[:max_trades].copy()
    split_at = int(len(frame) * train_fraction)
    if split_at < 1 or split_at >= len(frame):
        raise ValueError("train/test split must contain at least one row on each side")
    # Keep all rows with the same exchange timestamp on one side. This avoids
    # a subtle boundary leak when a source has a large group of trades sharing
    # one millisecond timestamp.
    cutoff_time = int(frame.event_time.iloc[split_at])
    train = frame[frame.event_time < cutoff_time].copy()
    test = frame[frame.event_time >= cutoff_time].copy()
    if train.empty or test.empty:
        # If the requested percentile lands inside one all-equal timestamp
        # block, choose the nearest valid timestamp boundary instead.
        unique_times = frame.event_time.drop_duplicates().tolist()
        boundary = max(1, min(len(unique_times) - 1, int(len(unique_times) * train_fraction)))
        cutoff_time = int(unique_times[boundary])
        train = frame[frame.event_time < cutoff_time].copy()
        test = frame[frame.event_time >= cutoff_time].copy()
    if train.empty or test.empty:
        raise ValueError("train/test split has no timestamp boundary with rows on both sides")
    return train.reset_index(drop=True), test.reset_index(drop=True)


def fit_train_parameters(
    train_trades: pd.DataFrame,
    *,
    inventory_limit: float,
    gamma_override: float | None = None,
    kappa_override: float | None = None,
) -> dict[str, Any]:
    """Fit sigma, kappa, and gamma using train observations only.

    The existing calibration functions are intentionally reused.  For an
    aggTrades source, sigma is estimated from trade prices and kappa uses the
    existing arrival-rate estimator; no test row is inspected here.
    """
    frame = _normalise_trades(train_trades)
    sigma = calibrate_sigma(frame, time_col="event_time", price_col="price")
    fitted_kappa = calibrate_kappa(frame, time_col="event_time")
    fitted_gamma = calibrate_gamma(sigma, inventory_limit)
    gamma = fitted_gamma if gamma_override is None else float(gamma_override)
    kappa = fitted_kappa if kappa_override is None else float(kappa_override)
    if gamma <= 0.0 or kappa <= 0.0:
        raise ValueError("fitted gamma and kappa must be positive")
    return {
        "fit_scope": "train_only",
        "fit_rows": len(frame),
        "fit_start": int(frame.event_time.iloc[0]),
        "fit_end": int(frame.event_time.iloc[-1]),
        "sigma": float(sigma),
        "kappa": float(kappa),
        "gamma": float(gamma),
        "gamma_fitted": gamma_override is None,
        "kappa_fitted": kappa_override is None,
        "sigma_method": "trade_price_scaled_differences",
        "kappa_method": "arrival_rate_heuristic",
        "gamma_method": "calibrate_gamma" if gamma_override is None else "cli_override",
    }


def run_walk_forward(
    trades: pd.DataFrame,
    *,
    config: WalkForwardConfig | None = None,
    max_trades: int | None = None,
) -> WalkForwardResult:
    """Fit on the chronological prefix and evaluate fixed/AS/no-trade on OOS."""
    settings = config or WalkForwardConfig()
    train, test = chronological_split(
        trades,
        train_fraction=settings.train_fraction,
        max_trades=max_trades,
    )
    calibration = fit_train_parameters(
        train,
        inventory_limit=settings.inventory_limit,
        gamma_override=settings.gamma,
        kappa_override=settings.kappa,
    )
    backtest_config = BacktestConfig(
        name="walk_forward_test",
        quote_interval_ms=settings.quote_interval_ms,
        flatten_at_end=settings.flatten_at_end,
        taker_fee_rate=settings.taker_fee_rate,
        execution=ExecutionAssumptions(maker_fee_rate=settings.maker_fee_rate),
        risk=MarketMakingRiskConfig(
            max_position=settings.inventory_limit,
            max_order_qty=min(settings.qty, settings.inventory_limit),
        ),
    )
    replay = HistoricalReplay.from_binance_dataframe(test, config=backtest_config)
    sigma_override = calibration["sigma"] if calibration["sigma"] > 0.0 else None
    factories = {
        "fixed": lambda: FixedSpreadStrategy(
            half_spread=settings.fixed_half_spread,
            qty=settings.qty,
        ),
            "as": lambda: AvellanedaStoikovStrategy(
                gamma=calibration["gamma"],
                kappa=calibration["kappa"],
                qty=settings.qty,
                horizon_seconds=settings.horizon,
                min_spread=settings.spread_floor,
            sigma_override=sigma_override,
        ),
        "no_trade": lambda: NoTradeStrategy(),
    }
    results = {name: replay.run(factory) for name, factory in factories.items()}
    return WalkForwardResult(
        config=settings.to_dict(),
        train_rows=len(train),
        test_rows=len(test),
        train_start=int(train.event_time.iloc[0]),
        train_end=int(train.event_time.iloc[-1]),
        test_start=int(test.event_time.iloc[0]),
        test_end=int(test.event_time.iloc[-1]),
        calibration=calibration,
        results=results,
    )


__all__ = [
    "WalkForwardConfig",
    "WalkForwardResult",
    "chronological_split",
    "fit_train_parameters",
    "hash_dataframe",
    "run_walk_forward",
]
