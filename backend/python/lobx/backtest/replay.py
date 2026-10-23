"""Historical BTCUSDT replay using captured depth and trade Parquet files.

The runner consumes only data available at each event timestamp.  It requires
the capture to begin with a persisted depth snapshot; replaying bare depth
diffs without a starting snapshot is deliberately rejected because it would
silently create a wrong order book.
"""
from __future__ import annotations

import json
import warnings
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pandas as pd

from lobx.analytics.volatility import TimestampAwareRollingRealizedVolatility
from lobx.analytics.adverse_selection import classify_markout, summarise_adverse_selection
from lobx.analytics.metrics import compute_risk_adjusted_metrics
from lobx.analytics.pnl_summary import summarize_fills
from lobx.analytics.provenance import git_provenance, parameter_hash
from lobx.analytics.pnl_waterfall import summarize_pnl_waterfall
from lobx.analytics.research import quote_quality
from lobx.execution.shadow_engine import ExecutionAssumptions, ShadowQuoteEngine
from lobx.market_data.depth_stream_manager import LocalOrderBook
from lobx.market_data.trade_stream import TradeTick
from lobx.risk import MarketMakingRiskConfig, MarketMakingRiskManager
from lobx.strategy.base import MarketState, Strategy


DATA_SOURCE_REAL_L2 = "real_l2"
DATA_SOURCE_SYNTHETIC_FROM_TRADES = "synthetic_from_trades"


def _validate_data_source(data_source: str) -> str:
    if data_source not in {
        DATA_SOURCE_REAL_L2,
        DATA_SOURCE_SYNTHETIC_FROM_TRADES,
    }:
        raise ValueError(
            "data_source must be 'real_l2' or 'synthetic_from_trades'"
        )
    return data_source


@dataclass(frozen=True)
class BacktestConfig:
    """Reproducible assumptions for one backtest run.

    ``quote_interval_ms`` deliberately bounds quote churn. A strategy sees
    every depth event for marking and volatility, but can only cancel/replace
    at this cadence. This is closer to an exchange gateway than reacting to
    every microscopic depth update.
    
    ``duration_ms`` controls simulation length:
        - None: process entire dataset
        - int: stop after this many milliseconds
    """

    name: str = "baseline"
    quote_interval_ms: int = 100
    duration_ms: int | None = None
    markout_horizon_ms: int = 1_000
    markout_horizons_ms: tuple[int, ...] = (100, 500, 1_000, 5_000, 10_000, 30_000)
    flatten_at_end: bool = True
    taker_fee_rate: float = 0.0005
    execution: ExecutionAssumptions = field(default_factory=ExecutionAssumptions)
    risk: MarketMakingRiskConfig = field(default_factory=MarketMakingRiskConfig)
    realized_vol_window_ms: int = 60_000

    def __post_init__(self) -> None:
        if self.quote_interval_ms < 0 or self.markout_horizon_ms < 0:
            raise ValueError("intervals must be non-negative")
        if self.duration_ms is not None and self.duration_ms <= 0:
            raise ValueError("duration_ms must be positive")
        if self.taker_fee_rate < 0:
            raise ValueError("taker_fee_rate cannot be negative")
        if self.realized_vol_window_ms <= 0:
            raise ValueError("realized_vol_window_ms must be positive")
        if any(horizon <= 0 for horizon in self.markout_horizons_ms):
            raise ValueError("markout horizons must be positive")
        if self.markout_horizon_ms not in self.markout_horizons_ms:
            object.__setattr__(
                self,
                "markout_horizons_ms",
                (*self.markout_horizons_ms, self.markout_horizon_ms),
            )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class BacktestResult:
    """Auditable outputs for one strategy/scenario pair."""

    strategy: str
    config: dict
    started_at: int
    ended_at: int
    depth_events: int
    trade_events: int
    fills: list[dict]
    events: list[dict]
    markouts: list[dict]
    equity_curve: list[dict]
    summary: dict
    data_source: str = DATA_SOURCE_REAL_L2
    execution_counters: dict[str, dict] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def write(self, output_dir: str | Path) -> Path:
        """Write a self-contained JSON summary and CSV audit trails."""
        path = Path(output_dir)
        path.mkdir(parents=True, exist_ok=True)
        stem = f"{self.strategy}_{self.config['name']}"
        safe_stem = "".join(c if c.isalnum() or c in "-_" else "_" for c in stem)
        report_path = path / f"{safe_stem}_summary.json"
        report_path.write_text(
            json.dumps(
                {
                    "strategy": self.strategy,
                    "config": self.config,
                    "started_at": self.started_at,
                    "ended_at": self.ended_at,
                    "data_source": self.data_source,
                    "depth_events": self.depth_events,
                    "trade_events": self.trade_events,
                    "execution_counters": self.execution_counters,
                    "warnings": self.warnings,
                    "provenance": git_provenance(),
                    "parameter_hash": parameter_hash(self.config),
                    "summary": self.summary,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        pd.DataFrame(self.fills).to_csv(path / f"{safe_stem}_fills.csv", index=False)
        pd.DataFrame(self.events).to_csv(path / f"{safe_stem}_events.csv", index=False)
        pd.DataFrame(self.markouts).to_csv(path / f"{safe_stem}_markouts.csv", index=False)
        pd.DataFrame(self.equity_curve).to_csv(path / f"{safe_stem}_equity.csv", index=False)
        return report_path


class HistoricalReplay:
    """Replay L2 depth and trade captures through a strategy and paper broker."""

    DEPTH_COLUMNS = ["event_time", "side", "price", "qty"]
    TRADE_COLUMNS = ["event_time", "trade_id", "price", "qty", "is_buyer_maker", "symbol"]

    def __init__(
        self,
        config: BacktestConfig | None = None,
        *,
        data_source: str = DATA_SOURCE_REAL_L2,
    ) -> None:
        self.config = config or BacktestConfig()
        self.data_source = _validate_data_source(data_source)

    @classmethod
    def from_parquet(
        cls,
        depth_path: str | Path,
        trades_path: str | Path,
        config: BacktestConfig | None = None,
    ) -> "CapturedReplay":
        """Load one closed capture. Files are sorted stably if necessary."""
        depth = pd.read_parquet(depth_path, columns=cls.DEPTH_COLUMNS)
        trades = pd.read_parquet(trades_path, columns=cls.TRADE_COLUMNS)
        return CapturedReplay(
            depth=depth,
            trades=trades,
            config=config,
            data_source=DATA_SOURCE_REAL_L2,
        )

    @classmethod
    def from_binance_parquet(
        cls,
        trades_parquet_path: str | Path,
        config: BacktestConfig | None = None,
        spread_ticks: float = 0.20,
    ) -> "CapturedReplay":
        """Load a Binance Vision aggTrades Parquet file and generate deterministic two-sided depth ticks."""
        return cls.from_binance_dataframe(
            pd.read_parquet(trades_parquet_path),
            config=config,
            spread_ticks=spread_ticks,
        )

    @classmethod
    def from_binance_dataframe(
        cls,
        trades: pd.DataFrame,
        config: BacktestConfig | None = None,
        spread_ticks: float = 0.20,
    ) -> "CapturedReplay":
        """Build the deterministic aggTrades replay from an in-memory frame.

        Keeping this conversion separate from Parquet I/O lets validation and
        walk-forward callers split a source chronologically without writing
        train/test files back into the dataset directory.
        """
        df_trades = trades.copy()
        renames = {"transact_time": "event_time", "agg_trade_id": "trade_id"}
        for k, v in renames.items():
            if k in df_trades.columns:
                df_trades.rename(columns={k: v}, inplace=True)
        required = {"event_time", "trade_id", "price", "qty", "is_buyer_maker"}
        missing = required - set(df_trades.columns)
        if missing:
            raise ValueError(f"Binance trade data missing columns: {sorted(missing)}")
        if df_trades.empty:
            raise ValueError("Binance trade data is empty")
        if "symbol" not in df_trades.columns:
            df_trades["symbol"] = "BTCUSDT"

        df_trades = df_trades.sort_values(["event_time", "trade_id"]).reset_index(drop=True)

        depth_rows = []
        t0 = int(df_trades.event_time.iloc[0])
        p0 = float(df_trades.price.iloc[0])
        half = spread_ticks / 2.0

        def append_snapshot(timestamp: int, price: float, quantity: float) -> None:
            for level in range(60):
                depth_rows.append({
                    "event_time": timestamp,
                    "side": "bid",
                    "price": round(price - half - level * 0.1, 2),
                    "qty": quantity,
                })
                depth_rows.append({
                    "event_time": timestamp,
                    "side": "ask",
                    "price": round(price + half + level * 0.1, 2),
                    "qty": quantity,
                })

        append_snapshot(t0, p0, 1.0)

        last_p = p0
        for row in df_trades.itertuples(index=False):
            px = float(row.price)
            if abs(px - last_p) >= 0.05:
                t = int(row.event_time)
                # Replace the complete synthetic snapshot.  Clearing only the
                # old top level leaves stale deeper asks/bids behind when the
                # tape moves several ticks, which can cross the reconstructed
                # book and invalidate the OOS replay.
                for level in range(60):
                    depth_rows.append({
                        "event_time": t,
                        "side": "bid",
                        "price": round(last_p - half - level * 0.1, 2),
                        "qty": 0.0,
                    })
                    depth_rows.append({
                        "event_time": t,
                        "side": "ask",
                        "price": round(last_p + half + level * 0.1, 2),
                        "qty": 0.0,
                    })
                append_snapshot(t, px, 2.0)
                last_p = px

        # Preserve append order for equal timestamps.  A trade-tape conversion
        # can generate several absolute updates in one millisecond; sorting
        # those rows by side/price would reorder clears and replacements and
        # could leave the reconstructed book crossed or empty.
        df_depth = pd.DataFrame(depth_rows).sort_values(
            ["event_time"], kind="stable"
        ).reset_index(drop=True)
        return CapturedReplay(
            depth=df_depth,
            trades=df_trades,
            config=config,
            data_source=DATA_SOURCE_SYNTHETIC_FROM_TRADES,
        )

    def run(
        self,
        depth: pd.DataFrame,
        trades: pd.DataFrame,
        strategy_factory: Callable[[], Strategy],
    ) -> BacktestResult:
        """Run one deterministic strategy replay.

        Equal timestamps use the documented order: apply depth, make a quote,
        then process trades. Nonzero ``latency_ms`` is therefore mandatory for
        conservative runs and prevents same-timestamp information leakage.
        """
        depth = self._normalise_depth(depth)
        trades = self._normalise_trades(trades)
        self._validate_bootstrap(depth)

        warning_messages: list[str] = []
        if (
            self.config.execution.queue_model == "conservative"
            and self.data_source == DATA_SOURCE_SYNTHETIC_FROM_TRADES
        ):
            message = (
                "conservative queue mode is running on synthetic depth "
                "(data_source=synthetic_from_trades); queue-advance results "
                "are diagnostic only"
            )
            warnings.warn(message, UserWarning, stacklevel=2)
            warning_messages.append(message)

        strategy = strategy_factory()
        book = LocalOrderBook()
        broker = ShadowQuoteEngine(assumptions=self.config.execution)
        risk = MarketMakingRiskManager(self.config.risk)
        vol = TimestampAwareRollingRealizedVolatility(window_ms=self.config.realized_vol_window_ms)

        # Keep iterators lazy: a full overnight capture has hundreds of
        # thousands of timestamp groups, and materialising them all wastes
        # more memory than the capture itself.
        depth_iter = iter(depth.groupby("event_time", sort=False))
        trade_iter = iter(trades.groupby("event_time", sort=False))
        current_depth = next(depth_iter, None)
        current_trade = next(trade_iter, None)
        next_quote_at: int | None = None
        last_mid: float | None = None
        max_abs_position = 0.0
        inventory_sum = 0.0
        inventory_observations = 0
        equity_curve: list[dict] = []
        pending_markouts: list[dict] = []
        markout_records: list[dict] = []
        depth_event_count = 0
        trade_event_count = 0
        started_at = int(depth.event_time.iloc[0])
        if not trades.empty:
            started_at = min(started_at, int(trades.event_time.iloc[0]))
        ended_at = int(depth.event_time.iloc[-1])
        if not trades.empty:
            ended_at = max(ended_at, int(trades.event_time.iloc[-1]))
        
        # Apply duration limit if configured
        if self.config.duration_ms is not None:
            ended_at = min(ended_at, started_at + self.config.duration_ms)

        # Track most recent trade info to include in MarketState
        last_trade_price: float | None = None
        last_trade_qty: float | None = None
        last_trade_side: str | None = None
        last_trade_timestamp: int | None = None

        while current_depth is not None or current_trade is not None:
            depth_ts = int(current_depth[0]) if current_depth is not None else None
            trade_ts = int(current_trade[0]) if current_trade is not None else None
            timestamp = min(t for t in (depth_ts, trade_ts) if t is not None)
            
            # Check duration limit
            if timestamp > ended_at:
                break

            if depth_ts == timestamp:
                group = current_depth[1]
                for row in group.itertuples(index=False):
                    book.apply_levels(row.side, [(float(row.price), float(row.qty))])
                    broker.on_depth_update(
                        side=str(row.side),
                        price=float(row.price),
                        qty=float(row.qty),
                        timestamp=timestamp,
                    )
                depth_event_count += 1
                current_depth = next(depth_iter, None)

                bid = book.best_bid()
                ask = book.best_ask()
                if bid is not None and ask is not None and bid[0] < ask[0]:
                    mid = (bid[0] + ask[0]) / 2.0
                    last_mid = mid
                    broker.update_market_context(
                        timestamp=timestamp,
                        mid=mid,
                        best_bid=bid[0],
                        best_ask=ask[0],
                    )
                    volatility = vol.update(timestamp, mid)
                    equity = broker.net_pnl(mid)
                    risk.update_equity(equity)
                    for pending in pending_markouts[:]:
                        if timestamp >= pending["due_at"]:
                            markout = pending["signed_qty"] * (mid - pending["price"])
                            record = {
                                "order_id": pending["order_id"],
                                "fill_timestamp": pending["fill_timestamp"],
                                "horizon_ms": pending["horizon_ms"],
                                "due_at": pending["due_at"],
                                "observed_at": timestamp,
                                "future_mid": mid,
                                "fill_price": pending["price"],
                                "markout": markout,
                                "available": True,
                                "classification": classify_markout(markout),
                            }
                            markout_records.append(record)
                            broker.emit_event({
                                "event_type": "MARKOUT",
                                "timestamp": timestamp,
                                **record,
                            })
                            pending_markouts.remove(pending)

                    if next_quote_at is None or timestamp >= next_quote_at:
                        state = MarketState.from_book(
                            timestamp=timestamp,
                            best_bid=bid[0],
                            best_bid_qty=bid[1],
                            best_ask=ask[0],
                            best_ask_qty=ask[1],
                            volatility=volatility,
                            inventory=broker.inventory.position,
                            log_return=vol.last_log_return,
                            trade_price=last_trade_price,
                            trade_quantity=last_trade_qty,
                            trade_side=last_trade_side,
                        )
                        risk_events_before = len(risk.events)
                        quotes = risk.apply_quotes(
                            strategy.on_tick(state),
                            broker.inventory.position,
                            timestamp=timestamp,
                            market_mid=mid,
                            # update_quotes() replaces current orders atomically.
                            open_orders=0,
                        )
                        for risk_event in risk.events[risk_events_before:]:
                            broker.emit_event(risk_event)
                        broker.record_quote_decision(
                            strategy=strategy.name(),
                            state=state,
                            quotes=quotes,
                            details=getattr(strategy, "last_quote_audit", {}),
                        )
                        bid_ahead = book.bids.get(quotes.bid.price, 0.0) if quotes.bid else 0.0
                        ask_ahead = book.asks.get(quotes.ask.price, 0.0) if quotes.ask else 0.0
                        broker.update_quotes(quotes, timestamp, bid_ahead, ask_ahead)
                        next_quote_at = timestamp + self.config.quote_interval_ms

                    max_abs_position = max(max_abs_position, abs(broker.inventory.position))
                    inventory_sum += abs(broker.inventory.position)
                    inventory_observations += 1
                    equity_curve.append(
                        {"timestamp": timestamp, "equity": equity, "position": broker.inventory.position}
                    )

            if trade_ts == timestamp:
                group = current_trade[1]
                fills_before = len(broker.fills)
                for row in group.itertuples(index=False):
                    # Capture trade info for MarketState
                    last_trade_timestamp = int(row.event_time)
                    last_trade_price = float(row.price)
                    last_trade_qty = float(row.qty)
                    last_trade_side = "sell" if bool(row.is_buyer_maker) else "buy"
                    
                    trade = TradeTick(
                        event_time=int(row.event_time),
                        trade_id=int(row.trade_id),
                        price=float(row.price),
                        qty=float(row.qty),
                        is_buyer_maker=bool(row.is_buyer_maker),
                        symbol=str(row.symbol),
                    )
                    broker.on_trade(trade)
                    hawkes_decision = risk.on_trade(trade)
                    if hawkes_decision is not None:
                        broker.emit_event({
                            "event_type": "HAWKES_DECISION",
                            "timestamp": hawkes_decision.timestamp_ms,
                            "intensity": hawkes_decision.intensity,
                            "ratio": hawkes_decision.ratio,
                            "regime": hawkes_decision.regime.value,
                            "cooldown_active": hawkes_decision.cooldown_active,
                            "quotes_pulled": hawkes_decision.quotes_pulled,
                            "spread_multiplier": hawkes_decision.spread_multiplier,
                        })
                for fill in broker.fills[fills_before:]:
                    if fill["order_id"] == "forced_flatten":
                        continue
                    for horizon_ms in self.config.markout_horizons_ms:
                        pending_markouts.append({
                            "order_id": fill["order_id"],
                            "fill_timestamp": fill["timestamp"],
                            "due_at": fill["timestamp"] + horizon_ms,
                            "horizon_ms": horizon_ms,
                            "price": fill["price"],
                            "signed_qty": fill["qty"] if fill["side"] == "buy" else -fill["qty"],
                        })
                trade_event_count += len(group)
                current_trade = next(trade_iter, None)
                max_abs_position = max(max_abs_position, abs(broker.inventory.position))

        if last_mid is None:
            raise ValueError("capture never produced a valid two-sided book")
        flatten_fee = 0.0
        if self.config.flatten_at_end and abs(broker.inventory.position) > 1e-12:
            bid, ask = book.best_bid(), book.best_ask()
            if bid is None or ask is None:
                raise ValueError("cannot flatten without a two-sided final book")
            side = "sell" if broker.inventory.position > 0 else "buy"
            price = bid[0] if side == "sell" else ask[0]
            qty = abs(broker.inventory.position)
            flatten_fee = price * qty * self.config.taker_fee_rate
            broker.portfolio.on_fill(side, price, qty, flatten_fee)
            flatten_pnl = broker.portfolio.snapshot(price)
            broker.emit_event(
                {
                    "event_type": "FORCED_FLATTEN",
                    "timestamp": ended_at,
                    "order_id": "forced_flatten",
                    "side": side,
                    "price": price,
                    "qty": qty,
                    "fee": flatten_fee,
                    "gross_pnl": flatten_pnl["gross_pnl"],
                    "net_pnl": flatten_pnl["net_pnl"],
                    "inventory": broker.inventory.position,
                    "realized_pnl": broker.inventory.realized_pnl,
                    "reason": "flatten_at_end",
                }
            )
            broker.fills.append(
                {
                    "order_id": "forced_flatten",
                    "side": side,
                    "price": price,
                    "qty": qty,
                    "trade_id": None,
                    "timestamp": ended_at,
                    "fee": flatten_fee,
                    "is_maker": False,
                    "gross_pnl": flatten_pnl["gross_pnl"],
                    "net_pnl": flatten_pnl["net_pnl"],
                }
            )

        for pending in pending_markouts:
            record = {
                "order_id": pending["order_id"],
                "fill_timestamp": pending["fill_timestamp"],
                "horizon_ms": pending["horizon_ms"],
                "due_at": pending["due_at"],
                "observed_at": None,
                "future_mid": None,
                "fill_price": pending["price"],
                "markout": None,
                "available": False,
                "classification": "UNAVAILABLE",
            }
            markout_records.append(record)
            broker.emit_event({
                "event_type": "MARKOUT_UNAVAILABLE",
                "timestamp": ended_at,
                **record,
                "reason": "no_future_book_observation",
            })

        available_markouts = [
            record for record in markout_records if record["available"]
        ]

        final_equity = broker.net_pnl(last_mid)
        equity_values = [point["equity"] for point in equity_curve] + [final_equity]
        max_drawdown = _max_drawdown_absolute(equity_values)
        return_period_ms = max(self.config.quote_interval_ms, 1)
        pnl_returns = _regularized_pnl_returns(
            equity_curve,
            final_equity,
            period_ms=return_period_ms,
        )
        risk_adjusted = compute_risk_adjusted_metrics(
            pnl_returns,
            period_seconds=return_period_ms / 1_000.0,
        )
        total_turnover = sum(fill["price"] * fill["qty"] for fill in broker.fills)
        forced_fills = [fill for fill in broker.fills if fill["order_id"] == "forced_flatten"]
        maker_fills = [fill for fill in broker.fills if fill["order_id"] != "forced_flatten"]
        summary = {
            "data_source": self.data_source,
            "final_mid": last_mid,
            "net_pnl": final_equity,
            "gross_pnl": broker.portfolio.gross_pnl(last_mid),
            "realized_pnl": broker.inventory.realized_pnl,
            "fees_paid": broker.fees_paid,
            "forced_flatten_fee": flatten_fee,
            "turnover": total_turnover,
            "fills": len(broker.fills),
            "maker_fill_count": len(maker_fills),
            "maker_filled_qty": sum(fill["qty"] for fill in maker_fills),
            "forced_flatten_count": len(forced_fills),
            "forced_flatten_qty": sum(fill["qty"] for fill in forced_fills),
            "final_position": broker.inventory.position,
            "max_abs_position": max_abs_position,
            "mean_abs_position": inventory_sum / inventory_observations if inventory_observations else 0.0,
            "average_inventory": broker.inventory.average_inventory,
            "inventory_variance": broker.inventory.inventory_variance,
            "inventory_half_life_ms": broker.inventory.inventory_half_life_ms,
            "time_spent_long_ms": broker.inventory.time_spent_long_ms,
            "time_spent_short_ms": broker.inventory.time_spent_short_ms,
            "inventory_distribution": broker.inventory.inventory_distribution,
            "max_drawdown": max_drawdown,
            # These are P&L increments, rather than percentage returns: the
            # replay intentionally has no initial capital assumption. The
            # observation period and annualization are emitted beside the
            # ratios so consumers cannot mistake the convention.
            "pnl_return_observations": len(pnl_returns),
            "pnl_return_period_ms": return_period_ms,
            "pnl_periods_per_year": risk_adjusted["periods_per_year"],
            "sharpe_ratio": risk_adjusted["sharpe_ratio"],
            "sortino_ratio": risk_adjusted["sortino_ratio"],
            "sharpe": risk_adjusted["sharpe_ratio"],
            "sortino": risk_adjusted["sortino_ratio"],
            "risk_adjusted_metrics": risk_adjusted,
            "mean_1s_markout": _mean_markout(markout_records, 1_000),
            "markout_observations": len(available_markouts),
            "risk_halted": risk.halted,
            "risk_halt_reason": risk.halt_reason,
        }
        summary.update(quote_quality(
            broker.events,
            broker.fills,
            start=started_at,
            end=ended_at,
        ))
        summary.update(summarise_adverse_selection(markout_records))
        summary.update(summarize_pnl_waterfall(
            broker.fills,
            markout_records,
            realized_pnl=broker.inventory.realized_pnl,
            unrealized_pnl=broker.inventory.unrealized_pnl(last_mid),
            fees=broker.fees_paid,
            markout_horizon_ms=self.config.markout_horizon_ms,
        ))
        summary["spread_capture_pnl"] = summary.get("execution_edge", 0.0)
        summary["markout_adverse_selection_pnl"] = summary.get(
            "adverse_selection_markout", 0.0
        )
        summary["decomposition_residual"] = summary["net_pnl"] - (
            summary["spread_capture_pnl"]
            + summary["markout_adverse_selection_pnl"]
            - summary["fees"]
        )
        summary["decomposition_reconciled"] = abs(summary["decomposition_residual"]) <= 1e-9
        summary.update(summarize_fills(
            broker.fills,
            final_mid=last_mid,
            maker_bps=self.config.execution.maker_fee_rate * 10_000.0,
            taker_bps=self.config.taker_fee_rate * 10_000.0,
        ))
        summary.update({
            "maker_fee_bps": self.config.execution.maker_fee_rate * 10_000.0,
            "taker_fee_bps": self.config.taker_fee_rate * 10_000.0,
        })
        summary.update({
            f"mean_{horizon_ms}ms_markout": _mean_markout(markout_records, horizon_ms)
            for horizon_ms in self.config.markout_horizons_ms
        })

        mode = self.config.execution.queue_model
        maker_fills = [
            fill for fill in broker.fills if fill["order_id"] != "forced_flatten"
        ]
        execution_counters = {
            mode: {
                "quotes_placed": broker.quotes_placed,
                "fills": len(maker_fills),
                "filled_qty": sum(fill["qty"] for fill in maker_fills),
                "mean_queue_ahead_at_placement": broker.mean_queue_ahead_at_placement,
                "depth_advance_events": broker.depth_advance_events,
                "gross": broker.portfolio.gross_pnl(last_mid),
                "fees": broker.fees_paid,
                "net": broker.net_pnl(last_mid),
                # Keep the session-level distinction visible when flattening is
                # enabled, while the requested counters remain mode-specific.
                "forced_flatten_fills": len(forced_fills),
                "forced_flatten_qty": sum(fill["qty"] for fill in forced_fills),
            }
        }
        summary["execution_counters"] = execution_counters
        summary["execution_mode"] = mode
        summary["warnings"] = warning_messages
        return BacktestResult(
            strategy=strategy.name(),
            config=self.config.to_dict(),
            started_at=started_at,
            ended_at=ended_at,
            depth_events=depth_event_count,
            trade_events=trade_event_count,
            fills=broker.fills,
            events=broker.events,
            markouts=markout_records,
            equity_curve=equity_curve,
            summary=summary,
            data_source=self.data_source,
            execution_counters=execution_counters,
            warnings=warning_messages,
        )

    @staticmethod
    def _normalise_depth(depth: pd.DataFrame) -> pd.DataFrame:
        required = set(HistoricalReplay.DEPTH_COLUMNS)
        if not required.issubset(depth.columns):
            raise ValueError(f"depth capture missing columns: {sorted(required - set(depth.columns))}")
        result = depth[HistoricalReplay.DEPTH_COLUMNS].copy()
        if not result.event_time.is_monotonic_increasing:
            result = result.sort_values(["event_time", "side", "price"], kind="stable")
        return result.reset_index(drop=True)

    @staticmethod
    def _normalise_trades(trades: pd.DataFrame) -> pd.DataFrame:
        required = set(HistoricalReplay.TRADE_COLUMNS)
        if not required.issubset(trades.columns):
            raise ValueError(f"trade capture missing columns: {sorted(required - set(trades.columns))}")
        result = trades[HistoricalReplay.TRADE_COLUMNS].copy()
        if not result.event_time.is_monotonic_increasing:
            result = result.sort_values(["event_time", "trade_id"], kind="stable")
        return result.reset_index(drop=True)

    @staticmethod
    def _validate_bootstrap(depth: pd.DataFrame) -> None:
        if depth.empty:
            raise ValueError("depth capture is empty")
        first = depth[depth.event_time == depth.event_time.iloc[0]]
        has_bids = (first.side == "bid").any()
        has_asks = (first.side == "ask").any()
        # A normal diff has a handful of rows. Captures produced by this
        # collector persist the initial 1000-level REST snapshot as >100 rows.
        if len(first) < 100 or not (has_bids and has_asks):
            raise ValueError(
                "capture has no persisted initial depth snapshot; refusing an invalid replay"
            )


class CapturedReplay(HistoricalReplay):
    """A replay with its Parquet data loaded once for multi-scenario runs."""

    def __init__(
        self,
        depth: pd.DataFrame,
        trades: pd.DataFrame,
        config: BacktestConfig | None,
        *,
        data_source: str = DATA_SOURCE_REAL_L2,
    ) -> None:
        super().__init__(config, data_source=data_source)
        self.depth = depth
        self.trades = trades

    def run(self, strategy_factory: Callable[[], Strategy]) -> BacktestResult:  # type: ignore[override]
        return super().run(self.depth, self.trades, strategy_factory)


def _max_drawdown_absolute(equity: list[float]) -> float:
    peak = 0.0
    max_drawdown = 0.0
    for value in equity:
        peak = max(peak, value)
        max_drawdown = max(max_drawdown, peak - value)
    return max_drawdown


def _mean_markout(records: list[dict], horizon_ms: int) -> float | None:
    values = [
        record["markout"]
        for record in records
        if record["horizon_ms"] == horizon_ms
        and record["available"]
        and record["markout"] is not None
    ]
    return sum(values) / len(values) if values else None


def _regularized_pnl_returns(
    equity_curve: list[dict],
    final_equity: float,
    *,
    period_ms: int,
) -> list[float]:
    """Convert irregular equity marks into fixed-period P&L increments.

    The last available mark is carried forward to each grid point. This is
    causal (a grid point never sees a future event), gives Sharpe/Sortino a
    meaningful observation period, and makes the convention explicit in the
    replay summary.
    """
    points = sorted(
        (
            int(point["timestamp"]),
            float(point["equity"]),
        )
        for point in equity_curve
        if point.get("timestamp") is not None and point.get("equity") is not None
    )
    if not points:
        return []
    period_ms = max(int(period_ms), 1)
    start = points[0][0]
    end = max(points[-1][0], start)
    grid = list(range(start, end + period_ms, period_ms))
    if not grid or grid[-1] < end:
        grid.append(end)
    if grid[-1] < points[-1][0]:
        grid.append(points[-1][0])

    values: list[float] = []
    point_index = 0
    current = points[0][1]
    for timestamp in grid:
        while point_index + 1 < len(points) and points[point_index + 1][0] <= timestamp:
            point_index += 1
            current = points[point_index][1]
        values.append(current)
    if values[-1] != float(final_equity):
        values.append(float(final_equity))
    return [current - previous for previous, current in zip(values, values[1:])]
