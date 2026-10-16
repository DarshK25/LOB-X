"""Run a causal, trade-tape-only backtest over Binance aggTrades Parquet files.

This runner deliberately does *not* call ``HistoricalReplay.from_binance_parquet``.
That existing helper creates synthetic depth around trades, which is useful for a
smoke test but is not appropriate when the requirement is to avoid fabricated
market data.

The two supplied files are Binance Futures ``aggTrades`` archives.  They contain
real executed trades, but they do not contain bid/ask L2 snapshots.  Therefore
this runner uses only the observed last-trade price as the strategy reference
price and uses only subsequent observed trades for paper fills.  It does not claim
to be an L2 or queue-priority backtest.

Execution rules are intentionally explicit:

* every input row is processed in file order; timestamp regressions abort the run;
* a trade fills a previously active bid only when it is a seller aggressor and
  its observed price is at or below that bid;
* a trade fills a previously active ask only when it is a buyer aggressor and
  its observed price is at or above that ask;
* fills occur at the strategy quote price, with no queue model because the files
  have no L2/L3 depth;
* quote activation latency defaults to 100 ms;
* no maker/taker fees are invented because the input files do not contain an
  account fee schedule;
* inventory is not forcibly flattened at the end; final P&L is marked at the
  final observed trade price.

The strategy implementations are the project's real FixedSpreadStrategy,
InventorySkewStrategy, and AvellanedaStoikovStrategy.  This is a genuine
trade-tape paper backtest, not a claim of exchange-realistic L2 execution.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

import numpy as np
import pyarrow.parquet as pq

_BACKEND = Path(__file__).resolve().parents[1] / "backend" / "python"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from lobx.analytics.volatility import EWMAVolatilityEstimator
from lobx.execution.inventory import InventoryTracker
from lobx.execution.portfolio import PortfolioEngine
from lobx.market_data.trade_stream import TradeTick
from lobx.risk import MarketMakingRiskConfig, MarketMakingRiskManager
from lobx.strategy.avellaneda import AvellanedaStoikovStrategy
from lobx.strategy.base import MarketState, Quote, QuotePair, Strategy
from lobx.strategy.fixed_spread import FixedSpreadStrategy
from lobx.strategy.inventory_skew import InventorySkewStrategy


REQUIRED_COLUMNS = {
    "agg_trade_id",
    "price",
    "qty",
    "transact_time",
    "is_buyer_maker",
    "symbol",
}
INPUT_COLUMNS = [
    "agg_trade_id",
    "price",
    "qty",
    "transact_time",
    "is_buyer_maker",
    "symbol",
]


@dataclass
class TapeOrder:
    side: str
    price: float
    qty: float
    remaining_qty: float
    placed_at: int
    active_at: int


class TradeTapePaperBroker:
    """Small-memory paper broker for actual trade-tape observations."""

    def __init__(
        self,
        *,
        strategy_name: str,
        fills_path: Path,
        latency_ms: int,
        risk_config: MarketMakingRiskConfig,
    ) -> None:
        self.strategy_name = strategy_name
        self.latency_ms = latency_ms
        self.portfolio = PortfolioEngine()
        self.risk = MarketMakingRiskManager(risk_config)
        self.bid: TapeOrder | None = None
        self.ask: TapeOrder | None = None
        self.fills = 0
        self.maker_filled_qty = 0.0
        self.turnover = 0.0
        self.execution_edge = 0.0
        self.max_abs_position = 0.0
        self.max_drawdown = 0.0
        self.peak_equity = 0.0
        self.risk_block_events = 0
        self.last_risk_reason: str | None = None
        self._fills_file = fills_path.open("w", newline="", encoding="utf-8")
        self._fills_writer = csv.DictWriter(
            self._fills_file,
            fieldnames=[
                "strategy",
                "trade_id",
                "timestamp_ms",
                "aggressor_side",
                "trade_price",
                "trade_qty",
                "quote_side",
                "quote_price",
                "fill_qty",
                "notional",
                "execution_edge",
                "position_after",
                "realized_pnl_after",
                "net_pnl_marked_at_trade",
            ],
        )
        self._fills_writer.writeheader()

    @property
    def inventory(self) -> InventoryTracker:
        return self.portfolio.inventory

    @property
    def position(self) -> float:
        return self.inventory.position

    def net_pnl(self, mark_price: float) -> float:
        return self.portfolio.net_pnl(mark_price)

    def _record_mark(self, timestamp: int, mark_price: float) -> None:
        self.inventory.observe(timestamp)
        equity = self.net_pnl(mark_price)
        self.peak_equity = max(self.peak_equity, equity)
        drawdown = self.peak_equity - equity
        self.max_drawdown = max(self.max_drawdown, drawdown)
        self.max_abs_position = max(self.max_abs_position, abs(self.position))

    def _fill_order(self, order: TapeOrder, trade: TradeTick) -> None:
        available = min(order.remaining_qty, trade.qty)
        if available <= 0.0:
            return

        side = "buy" if order.side == "bid" else "sell"
        edge = (
            (trade.price - order.price) * available
            if side == "buy"
            else (order.price - trade.price) * available
        )
        self.portfolio.on_fill(side, order.price, available, fee=0.0)
        order.remaining_qty -= available
        self.fills += 1
        self.maker_filled_qty += available
        self.turnover += order.price * available
        self.execution_edge += edge
        self._fills_writer.writerow(
            {
                "strategy": self.strategy_name,
                "trade_id": trade.trade_id,
                "timestamp_ms": trade.event_time,
                "aggressor_side": trade.taker_side,
                "trade_price": trade.price,
                "trade_qty": trade.qty,
                "quote_side": order.side,
                "quote_price": order.price,
                "fill_qty": available,
                "notional": order.price * available,
                "execution_edge": edge,
                "position_after": self.position,
                "realized_pnl_after": self.inventory.realized_pnl,
                "net_pnl_marked_at_trade": self.net_pnl(trade.price),
            }
        )

        if order.remaining_qty <= 1e-12:
            if order.side == "bid":
                self.bid = None
            else:
                self.ask = None

    def process_trade(self, trade: TradeTick) -> None:
        """Match one actual trade against quotes created by earlier events."""
        self._record_mark(trade.event_time, trade.price)
        if (
            self.bid is not None
            and trade.is_buyer_maker
            and trade.event_time >= self.bid.active_at
            and trade.price <= self.bid.price
        ):
            self._fill_order(self.bid, trade)
        if (
            self.ask is not None
            and not trade.is_buyer_maker
            and trade.event_time >= self.ask.active_at
            and trade.price >= self.ask.price
        ):
            self._fill_order(self.ask, trade)

    @staticmethod
    def _same_quote(order: TapeOrder | None, quote: Quote | None) -> bool:
        return (
            order is not None
            and quote is not None
            and abs(order.price - quote.price) <= 1e-12
            and abs(order.qty - quote.qty) <= 1e-12
        )

    def _replace(self, current: TapeOrder | None, quote: Quote | None, side: str, timestamp: int) -> TapeOrder | None:
        if quote is None:
            return None
        if self._same_quote(current, quote):
            return current
        return TapeOrder(
            side=side,
            price=quote.price,
            qty=quote.qty,
            remaining_qty=quote.qty,
            placed_at=timestamp,
            active_at=timestamp + self.latency_ms,
        )

    def update_quotes(self, quotes: QuotePair, timestamp: int, mark_price: float) -> None:
        """Apply risk-filtered quotes and keep only the current pair."""
        self.risk.update_equity(self.net_pnl(mark_price))
        before = len(self.risk.events)
        permitted = self.risk.apply_quotes(
            quotes,
            self.position,
            timestamp=timestamp,
            market_mid=mark_price,
            open_orders=0,
        )
        new_events = self.risk.events[before:]
        self.risk_block_events += len(new_events)
        if new_events:
            self.last_risk_reason = str(new_events[-1].get("reason"))
        self.risk.events.clear()
        self.bid = self._replace(self.bid, permitted.bid, "bid", timestamp)
        self.ask = self._replace(self.ask, permitted.ask, "ask", timestamp)

    def summary(self, *, rows: int, first_timestamp: int, last_timestamp: int) -> dict:
        final_price = getattr(self, "last_price", 0.0)
        return {
            "strategy": self.strategy_name,
            "rows_processed": rows,
            "first_timestamp_ms": first_timestamp,
            "last_timestamp_ms": last_timestamp,
            "fills": self.fills,
            "maker_filled_qty": self.maker_filled_qty,
            "turnover": self.turnover,
            "execution_edge": self.execution_edge,
            "position": self.position,
            "realized_pnl": self.inventory.realized_pnl,
            "unrealized_pnl": self.inventory.unrealized_pnl(final_price),
            "gross_pnl": self.portfolio.gross_pnl(final_price),
            "net_pnl": self.net_pnl(final_price),
            "fees_paid": self.portfolio.fees,
            "max_abs_position": self.max_abs_position,
            "average_inventory": self.inventory.average_inventory,
            "max_drawdown": self.max_drawdown,
            "risk_halted": self.risk.halted,
            "risk_halt_reason": self.risk.halt_reason,
            "risk_block_events": self.risk_block_events,
            "last_risk_reason": self.last_risk_reason,
            "fill_model": "actual aggTrade crossing a previously active model quote",
            "fee_model": "zero; no account fee schedule is present in input data",
            "flattening": "disabled; final position is marked, not forcibly liquidated",
        }

    def close(self) -> None:
        self._fills_file.close()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _json_clean(value):
    if isinstance(value, dict):
        return {str(k): _json_clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_clean(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    return value


def _validate_input(path: Path) -> dict:
    parquet = pq.ParquetFile(path)
    columns = set(parquet.schema_arrow.names)
    missing = REQUIRED_COLUMNS - columns
    if missing:
        raise ValueError(f"{path} is missing required columns: {sorted(missing)}")
    if parquet.metadata.num_rows == 0:
        raise ValueError(f"{path} is empty")

    names = parquet.schema_arrow.names
    time_idx = names.index("transact_time")
    price_idx = names.index("price")
    stats = {
        "rows": parquet.metadata.num_rows,
        "row_groups": parquet.metadata.num_row_groups,
        "columns": names,
    }
    time_stats = []
    price_stats = []
    for index in range(parquet.metadata.num_row_groups):
        time_col = parquet.metadata.row_group(index).column(time_idx).statistics
        price_col = parquet.metadata.row_group(index).column(price_idx).statistics
        if time_col is not None:
            time_stats.append((int(time_col.min), int(time_col.max)))
        if price_col is not None:
            price_stats.append((float(price_col.min), float(price_col.max)))
    stats["first_timestamp_ms"] = min((item[0] for item in time_stats), default=None)
    stats["last_timestamp_ms"] = max((item[1] for item in time_stats), default=None)
    stats["price_min"] = min((item[0] for item in price_stats), default=None)
    stats["price_max"] = max((item[1] for item in price_stats), default=None)
    return stats


def _strategy_factories(horizon_seconds: float) -> dict[str, Callable[[], Strategy]]:
    return {
        "fixed_spread": lambda: FixedSpreadStrategy(half_spread=0.50, qty=0.001),
        "inventory_skew": lambda: InventorySkewStrategy(
            half_spread=0.50,
            qty=0.001,
            skew_usd_per_btc=50.0,
        ),
        "avellaneda_stoikov": lambda: AvellanedaStoikovStrategy(
            gamma=0.1,
            kappa=1.5,
            horizon_seconds=horizon_seconds,
            qty=0.001,
            min_spread=0.02,
        ),
    }


def _trade_from_row(row) -> TradeTick:
    return TradeTick(
        event_time=int(row["transact_time"]),
        trade_id=int(row["agg_trade_id"]),
        price=float(row["price"]),
        qty=float(row["qty"]),
        is_buyer_maker=bool(row["is_buyer_maker"]),
        symbol=str(row["symbol"]),
    )


def _build_tape_state(
    trade: TradeTick,
    *,
    volatility: float,
    inventory: float,
) -> MarketState:
    """Build a state with only observed trade price and no invented L2.

    ``MarketState`` is shared by the project's strategies and historically
    expects bid/ask fields.  For this tape-only path those fields are set to the
    observed trade price and spread is zero; they are not presented as exchange
    quotes.  The strategies used here consume the observed mid, volatility, and
    inventory.  No fabricated depth quantity is supplied.
    """
    return MarketState(
        timestamp=trade.event_time,
        best_bid=trade.price,
        best_ask=trade.price,
        mid_price=trade.price,
        spread=0.0,
        best_bid_qty=0.0,
        best_ask_qty=0.0,
        volatility=volatility,
        inventory=inventory,
        imbalance=0.0,
        microprice=None,
        microprice_deviation=0.0,
        log_return=0.0,
        trade_price=trade.price,
        trade_quantity=trade.qty,
        trade_side=trade.taker_side,
    )


def _run_file(
    path: Path,
    output_dir: Path,
    *,
    batch_size: int,
    latency_ms: int,
    max_position: float,
    max_drawdown: float,
    progress_every: int,
) -> dict:
    metadata = _validate_input(path)
    first_ts = int(metadata["first_timestamp_ms"])
    last_ts = int(metadata["last_timestamp_ms"])
    horizon_seconds = max((last_ts - first_ts) / 1000.0, 1.0)
    factories = _strategy_factories(horizon_seconds)
    stem = path.stem
    file_output = output_dir / stem
    file_output.mkdir(parents=True, exist_ok=True)

    risk_config = MarketMakingRiskConfig(
        max_position=max_position,
        max_order_qty=0.001,
        max_drawdown=max_drawdown,
    )
    brokers: dict[str, TradeTapePaperBroker] = {}
    strategies: dict[str, Strategy] = {}
    volatilities: dict[str, EWMAVolatilityEstimator] = {}
    for name, factory in factories.items():
        brokers[name] = TradeTapePaperBroker(
            strategy_name=name,
            fills_path=file_output / f"{name}_fills.csv",
            latency_ms=latency_ms,
            risk_config=risk_config,
        )
        strategies[name] = factory()
        volatilities[name] = EWMAVolatilityEstimator(decay=0.98)

    stats = {
        "rows_processed": 0,
        "trade_qty": 0.0,
        "buyer_aggressor_qty": 0.0,
        "seller_aggressor_qty": 0.0,
        "price_min": float("inf"),
        "price_max": float("-inf"),
        "first_timestamp_ms": None,
        "last_timestamp_ms": None,
        "timestamp_regressions": 0,
    }
    previous_timestamp: int | None = None
    parquet = pq.ParquetFile(path)
    try:
        for batch in parquet.iter_batches(batch_size=batch_size, columns=INPUT_COLUMNS):
            rows = batch.to_pylist()
            for row in rows:
                trade = _trade_from_row(row)
                if previous_timestamp is not None and trade.event_time < previous_timestamp:
                    stats["timestamp_regressions"] += 1
                    raise ValueError(
                        f"timestamp regression in {path.name}: "
                        f"{trade.event_time} after {previous_timestamp}"
                    )
                previous_timestamp = trade.event_time
                if stats["first_timestamp_ms"] is None:
                    stats["first_timestamp_ms"] = trade.event_time
                stats["last_timestamp_ms"] = trade.event_time
                stats["rows_processed"] += 1
                stats["trade_qty"] += trade.qty
                if trade.is_buyer_maker:
                    stats["seller_aggressor_qty"] += trade.qty
                else:
                    stats["buyer_aggressor_qty"] += trade.qty
                stats["price_min"] = min(stats["price_min"], trade.price)
                stats["price_max"] = max(stats["price_max"], trade.price)

                for name, broker in brokers.items():
                    broker.last_price = trade.price
                    broker.process_trade(trade)
                    volatility = volatilities[name].update(trade.price)
                    state = _build_tape_state(
                        trade,
                        volatility=volatility,
                        inventory=broker.position,
                    )
                    quotes = strategies[name].on_tick(state)
                    broker.update_quotes(quotes, trade.event_time, trade.price)

                if progress_every and stats["rows_processed"] % progress_every == 0:
                    print(
                        f"{path.name}: processed {stats['rows_processed']:,}/"
                        f"{metadata['rows']:,} trades; last={trade.event_time}",
                        flush=True,
                    )
    finally:
        for broker in brokers.values():
            broker.close()

    if stats["rows_processed"] != metadata["rows"]:
        raise RuntimeError(
            f"row-count mismatch for {path.name}: metadata={metadata['rows']} "
            f"processed={stats['rows_processed']}"
        )

    strategy_summaries = {}
    for name, broker in brokers.items():
        strategy_summaries[name] = broker.summary(
            rows=stats["rows_processed"],
            first_timestamp=stats["first_timestamp_ms"],
            last_timestamp=stats["last_timestamp_ms"],
        )

    result = {
        "input": {
            "file": str(path),
            "sha256": _sha256(path),
            **metadata,
        },
        "execution_assumptions": {
            "data_type": "Binance Futures aggTrades; no L2/L3 depth",
            "quote_reference": "observed last trade price",
            "fill_source": "subsequent observed aggTrade rows only",
            "latency_ms": latency_ms,
            "maker_fee_rate": 0.0,
            "queue_model": "not modeled; unavailable from aggTrades",
            "flatten_at_end": False,
            "max_position": max_position,
            "max_drawdown": max_drawdown,
            "as_gamma": 0.1,
            "as_kappa": 1.5,
            "as_kappa_status": "scenario assumption, not fitted from these files",
            "volatility": "EWMA of log returns between every observed trade",
        },
        "observed_statistics": stats,
        "strategies": strategy_summaries,
    }
    (file_output / "summary.json").write_text(
        json.dumps(_json_clean(result), indent=2), encoding="utf-8"
    )
    return result


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        description=(
            "Run a causal trade-tape-only paper backtest over real Binance "
            "Futures aggTrades Parquet files; no synthetic depth is generated."
        ),
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data/parquet"),
        help="Directory containing the supplied aggTrades Parquet files",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("results/trade_tape_backtest"),
    )
    parser.add_argument("--batch-size", type=int, default=100_000)
    parser.add_argument(
        "--latency-ms",
        type=int,
        default=100,
        help="Quote activation latency; this is an explicit model assumption",
    )
    parser.add_argument("--max-position", type=float, default=0.005)
    parser.add_argument("--max-drawdown", type=float, default=25.0)
    parser.add_argument(
        "--progress-every",
        type=int,
        default=1_000_000,
        help="Print progress after this many processed rows; 0 disables progress",
    )
    parser.add_argument(
        "--file",
        type=Path,
        action="append",
        help="Run one explicit Parquet file; repeat to select multiple files",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    if args.batch_size <= 0:
        raise SystemExit("--batch-size must be positive")
    if args.latency_ms < 0:
        raise SystemExit("--latency-ms cannot be negative")
    if args.progress_every < 0:
        raise SystemExit("--progress-every cannot be negative")

    paths = args.file or sorted(args.data_dir.glob("*.parquet"))
    if not paths:
        raise SystemExit(f"No Parquet files found in {args.data_dir}")
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise SystemExit(f"Missing input files: {missing}")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    print("=" * 88)
    print("LOB-X REAL TRADE-TAPE BACKTEST")
    print("=" * 88)
    print("Input files:")
    for path in paths:
        print(f"  - {path}")
    print("Execution: actual aggTrades only; no synthetic depth; no forced flattening")
    print(
        f"Assumptions: latency={args.latency_ms}ms, fees=0, "
        f"max_position={args.max_position}, max_drawdown={args.max_drawdown}"
    )

    results = []
    for path in paths:
        print(f"\nStarting full replay: {path.name}")
        results.append(
            _run_file(
                path,
                args.output_dir,
                batch_size=args.batch_size,
                latency_ms=args.latency_ms,
                max_position=args.max_position,
                max_drawdown=args.max_drawdown,
                progress_every=args.progress_every,
            )
        )
        print(f"Completed: {path.name}")

    combined: dict[str, dict] = {}
    for result in results:
        for name, summary in result["strategies"].items():
            target = combined.setdefault(
                name,
                {
                    "datasets": 0,
                    "rows_processed": 0,
                    "fills": 0,
                    "maker_filled_qty": 0.0,
                    "turnover": 0.0,
                    "execution_edge": 0.0,
                    "realized_pnl": 0.0,
                    "net_pnl": 0.0,
                    "fees_paid": 0.0,
                    "max_abs_position": 0.0,
                    "max_drawdown": 0.0,
                    "risk_halted_datasets": 0,
                    "risk_block_events": 0,
                },
            )
            target["datasets"] += 1
            target["rows_processed"] += summary["rows_processed"]
            for key in (
                "fills",
                "maker_filled_qty",
                "turnover",
                "execution_edge",
                "realized_pnl",
                "net_pnl",
                "fees_paid",
                "risk_block_events",
            ):
                target[key] += summary[key]
            target["max_abs_position"] = max(
                target["max_abs_position"], summary["max_abs_position"]
            )
            target["max_drawdown"] = max(target["max_drawdown"], summary["max_drawdown"])
            target["risk_halted_datasets"] += int(summary["risk_halted"])

    manifest = {
        "runner": "scripts/run_trade_tape_backtest.py",
        "purpose": "causal paper backtest using real Binance Futures aggTrades",
        "synthetic_depth_used": False,
        "input_files": [result["input"] for result in results],
        "execution_assumptions": results[0]["execution_assumptions"],
        "per_file_results": [
            {
                "file": result["input"]["file"],
                "summary": result["strategies"],
            }
            for result in results
        ],
        "combined_strategies": combined,
    }
    manifest_path = args.output_dir / "combined_summary.json"
    manifest_path.write_text(json.dumps(_json_clean(manifest), indent=2), encoding="utf-8")

    print("\n" + "=" * 88)
    print("COMBINED RESULTS")
    print("=" * 88)
    for name, summary in combined.items():
        print(
            f"{name:22s} rows={summary['rows_processed']:,} "
            f"fills={summary['fills']:,} net_pnl={summary['net_pnl']:+.6f} "
            f"maxDD={summary['max_drawdown']:.6f} "
            f"halted_datasets={summary['risk_halted_datasets']}"
        )
    print(f"\nCombined report: {manifest_path}")
    print("Per-file summaries and fill ledgers are under the same output directory.")


if __name__ == "__main__":
    main()
