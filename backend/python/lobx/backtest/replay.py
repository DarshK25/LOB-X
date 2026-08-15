"""Historical BTCUSDT replay using captured depth and trade Parquet files.

The runner consumes only data available at each event timestamp.  It requires
the capture to begin with a persisted depth snapshot; replaying bare depth
diffs without a starting snapshot is deliberately rejected because it would
silently create a wrong order book.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
from typing import Callable

import pandas as pd

from lobx.analytics.volatility import EWMAVolatilityEstimator
from lobx.execution.shadow_engine import ExecutionAssumptions, ShadowQuoteEngine
from lobx.market_data.depth_stream_manager import LocalOrderBook
from lobx.market_data.trade_stream import TradeTick
from lobx.risk import MarketMakingRiskConfig, MarketMakingRiskManager
from lobx.strategy.base import MarketState, Strategy


@dataclass(frozen=True)
class BacktestConfig:
    """Reproducible assumptions for one backtest run.

    ``quote_interval_ms`` deliberately bounds quote churn. A strategy sees
    every depth event for marking and volatility, but can only cancel/replace
    at this cadence. This is closer to an exchange gateway than reacting to
    every microscopic depth update.
    """

    name: str = "baseline"
    quote_interval_ms: int = 100
    markout_horizon_ms: int = 1_000
    flatten_at_end: bool = True
    taker_fee_rate: float = 0.0005
    execution: ExecutionAssumptions = field(default_factory=ExecutionAssumptions)
    risk: MarketMakingRiskConfig = field(default_factory=MarketMakingRiskConfig)

    def __post_init__(self) -> None:
        if self.quote_interval_ms < 0 or self.markout_horizon_ms < 0:
            raise ValueError("intervals must be non-negative")
        if self.taker_fee_rate < 0:
            raise ValueError("taker_fee_rate cannot be negative")

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
    equity_curve: list[dict]
    summary: dict

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
                    "depth_events": self.depth_events,
                    "trade_events": self.trade_events,
                    "summary": self.summary,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        pd.DataFrame(self.fills).to_csv(path / f"{safe_stem}_fills.csv", index=False)
        pd.DataFrame(self.equity_curve).to_csv(path / f"{safe_stem}_equity.csv", index=False)
        return report_path


class HistoricalReplay:
    """Replay L2 depth and trade captures through a strategy and paper broker."""

    DEPTH_COLUMNS = ["event_time", "side", "price", "qty"]
    TRADE_COLUMNS = ["event_time", "trade_id", "price", "qty", "is_buyer_maker", "symbol"]

    def __init__(self, config: BacktestConfig | None = None) -> None:
        self.config = config or BacktestConfig()

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
        return CapturedReplay(depth=depth, trades=trades, config=config)

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

        strategy = strategy_factory()
        book = LocalOrderBook()
        broker = ShadowQuoteEngine(assumptions=self.config.execution)
        risk = MarketMakingRiskManager(self.config.risk)
        vol = EWMAVolatilityEstimator(decay=0.98)

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
        markouts: list[float] = []
        depth_event_count = 0
        trade_event_count = 0
        started_at = int(depth.event_time.iloc[0])
        if not trades.empty:
            started_at = min(started_at, int(trades.event_time.iloc[0]))
        ended_at = int(depth.event_time.iloc[-1])
        if not trades.empty:
            ended_at = max(ended_at, int(trades.event_time.iloc[-1]))

        while current_depth is not None or current_trade is not None:
            depth_ts = int(current_depth[0]) if current_depth is not None else None
            trade_ts = int(current_trade[0]) if current_trade is not None else None
            timestamp = min(t for t in (depth_ts, trade_ts) if t is not None)

            if depth_ts == timestamp:
                group = current_depth[1]
                for row in group.itertuples(index=False):
                    book.apply_levels(row.side, [(float(row.price), float(row.qty))])
                depth_event_count += 1
                current_depth = next(depth_iter, None)

                bid = book.best_bid()
                ask = book.best_ask()
                if bid is not None and ask is not None and bid[0] < ask[0]:
                    mid = (bid[0] + ask[0]) / 2.0
                    last_mid = mid
                    volatility = vol.update(mid)
                    equity = broker.net_pnl(mid)
                    risk.update_equity(equity)
                    for pending in pending_markouts[:]:
                        if timestamp >= pending["due_at"]:
                            markouts.append(pending["signed_qty"] * (mid - pending["price"]))
                            pending_markouts.remove(pending)

                    if next_quote_at is None or timestamp >= next_quote_at:
                        state = MarketState(
                            timestamp=timestamp,
                            mid_price=mid,
                            best_bid=bid[0],
                            best_bid_qty=bid[1],
                            best_ask=ask[0],
                            best_ask_qty=ask[1],
                            spread=ask[0] - bid[0],
                            volatility=volatility,
                            inventory=broker.inventory.position,
                        )
                        quotes = risk.apply_quotes(strategy.on_tick(state), broker.inventory.position)
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
                    broker.on_trade(
                        TradeTick(
                            event_time=int(row.event_time),
                            trade_id=int(row.trade_id),
                            price=float(row.price),
                            qty=float(row.qty),
                            is_buyer_maker=bool(row.is_buyer_maker),
                            symbol=str(row.symbol),
                        )
                    )
                for fill in broker.fills[fills_before:]:
                    pending_markouts.append(
                        {
                            "due_at": fill["timestamp"] + self.config.markout_horizon_ms,
                            "price": fill["price"],
                            "signed_qty": fill["qty"] if fill["side"] == "buy" else -fill["qty"],
                        }
                    )
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
            broker.inventory.on_fill(side, price, qty)
            flatten_fee = price * qty * self.config.taker_fee_rate
            broker.fees_paid += flatten_fee
            broker.fills.append(
                {
                    "order_id": "forced_flatten",
                    "side": side,
                    "price": price,
                    "qty": qty,
                    "trade_id": None,
                    "timestamp": ended_at,
                    "fee": flatten_fee,
                }
            )

        final_equity = broker.net_pnl(last_mid)
        equity_values = [point["equity"] for point in equity_curve] + [final_equity]
        max_drawdown = _max_drawdown_absolute(equity_values)
        total_turnover = sum(fill["price"] * fill["qty"] for fill in broker.fills)
        forced_fills = [fill for fill in broker.fills if fill["order_id"] == "forced_flatten"]
        maker_fills = [fill for fill in broker.fills if fill["order_id"] != "forced_flatten"]
        summary = {
            "net_pnl": final_equity,
            "gross_pnl": broker.inventory.total_pnl(last_mid),
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
            "max_drawdown": max_drawdown,
            "mean_1s_markout": sum(markouts) / len(markouts) if markouts else None,
            "markout_observations": len(markouts),
            "risk_halted": risk.halted,
            "risk_halt_reason": risk.halt_reason,
        }
        return BacktestResult(
            strategy=strategy.name(),
            config=self.config.to_dict(),
            started_at=started_at,
            ended_at=ended_at,
            depth_events=depth_event_count,
            trade_events=trade_event_count,
            fills=broker.fills,
            equity_curve=equity_curve,
            summary=summary,
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

    def __init__(self, depth: pd.DataFrame, trades: pd.DataFrame, config: BacktestConfig | None) -> None:
        super().__init__(config)
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
