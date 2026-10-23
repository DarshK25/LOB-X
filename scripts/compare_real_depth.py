"""Compare execution bounds on a selected slice of a real L2 capture.

The script replays one identical real-depth/trade slice under the three queue
assumptions.  It reconstructs a two-sided starting snapshot from the captured
L2 state at ``--start-ms`` so a slice beginning in the middle of a capture does
not silently replay diff-only data.

Examples
--------
python scripts/compare_real_depth.py --date 2026-08-02 --start-ms 1754092800000 --end-ms 1754092860000
python scripts/compare_real_depth.py --depth data/raw_watch/depth_2026-08-02.parquet \
    --trades data/raw_watch/trades_2026-08-02.parquet --duration-ms 60000
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

_BACKEND = Path(__file__).resolve().parents[1] / "backend" / "python"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from lobx.backtest.replay import (  # noqa: E402
    DATA_SOURCE_REAL_L2,
    BacktestConfig,
    HistoricalReplay,
)
from lobx.execution.shadow_engine import ExecutionAssumptions  # noqa: E402
from lobx.analytics.provenance import git_provenance, parameter_hash  # noqa: E402
from lobx.risk import MarketMakingRiskConfig  # noqa: E402
from lobx.strategy.avellaneda import AvellanedaStoikovStrategy  # noqa: E402
from lobx.strategy.fixed_spread import FixedSpreadStrategy  # noqa: E402
from lobx.strategy.inventory_skew import InventorySkewStrategy  # noqa: E402


def _slice_capture(
    depth: pd.DataFrame,
    trades: pd.DataFrame,
    *,
    start_ms: int | None,
    end_ms: int | None,
) -> tuple[pd.DataFrame, pd.DataFrame, int, int]:
    """Return a real-L2 slice with a reconstructed starting snapshot."""
    if depth.empty:
        raise ValueError("depth capture is empty")

    ordered_depth = depth.sort_values("event_time", kind="stable").reset_index(drop=True)
    capture_start = int(ordered_depth.event_time.iloc[0])
    capture_end = int(ordered_depth.event_time.iloc[-1])
    start = capture_start if start_ms is None else max(start_ms, capture_start)
    end = capture_end if end_ms is None else min(end_ms, capture_end)
    if end < start:
        raise ValueError(f"selected slice is empty: start_ms={start}, end_ms={end}")

    before_start = ordered_depth[ordered_depth.event_time <= start]
    if before_start.empty:
        before_start = ordered_depth.iloc[:1]
    levels = (
        before_start.sort_values("event_time", kind="stable")
        .drop_duplicates(subset=["side", "price"], keep="last")
    )
    levels = levels[levels.qty > 0.0].copy()
    levels["event_time"] = start

    later_depth = ordered_depth[
        (ordered_depth.event_time > start) & (ordered_depth.event_time <= end)
    ]
    sliced_depth = pd.concat(
        [levels[HistoricalReplay.DEPTH_COLUMNS], later_depth[HistoricalReplay.DEPTH_COLUMNS]],
        ignore_index=True,
    )
    sliced_trades = trades[
        (trades.event_time >= start) & (trades.event_time <= end)
    ][HistoricalReplay.TRADE_COLUMNS].copy()
    return sliced_depth, sliced_trades, start, end


def _strategy_factory(args: argparse.Namespace, horizon_seconds: float):
    if args.strategy == "fixed":
        return lambda: FixedSpreadStrategy(half_spread=args.half_spread, qty=args.qty)
    if args.strategy == "inventory-skew":
        return lambda: InventorySkewStrategy(
            half_spread=args.half_spread,
            qty=args.qty,
            skew_usd_per_btc=args.skew,
        )
    return lambda: AvellanedaStoikovStrategy(
        gamma=args.gamma,
        kappa=args.kappa,
        horizon_seconds=horizon_seconds,
        qty=args.qty,
        min_spread=args.min_spread,
    )


def _paths(args: argparse.Namespace) -> tuple[Path, Path]:
    if (args.depth is None) != (args.trades is None):
        raise SystemExit("--depth and --trades must be supplied together")
    if args.depth is not None:
        assert args.trades is not None
        return args.depth, args.trades
    if args.date is None:
        raise SystemExit("supply --date or both --depth and --trades")
    return (
        args.data_dir / f"depth_{args.date}.parquet",
        args.data_dir / f"trades_{args.date}.parquet",
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        description="Compare queue-mode bounds on a real_l2 depth/trade capture slice",
    )
    parser.add_argument("--date", help="UTC capture date used with --data-dir")
    parser.add_argument("--data-dir", type=Path, default=Path("data/raw"))
    parser.add_argument("--depth", type=Path, help="Explicit real depth Parquet path")
    parser.add_argument("--trades", type=Path, help="Explicit real trades Parquet path")
    parser.add_argument("--start-ms", type=int, help="Inclusive slice start, epoch milliseconds")
    parser.add_argument("--end-ms", type=int, help="Inclusive slice end, epoch milliseconds")
    parser.add_argument(
        "--duration-ms",
        type=int,
        help="Slice duration from --start-ms (or capture start when omitted)",
    )
    parser.add_argument(
        "--modes",
        nargs="+",
        choices=["shadow", "queue_ahead", "conservative"],
        default=["shadow", "queue_ahead", "conservative"],
    )
    parser.add_argument("--strategy", choices=["fixed", "as", "inventory-skew"], default="fixed")
    parser.add_argument("--qty", type=float, default=0.001)
    parser.add_argument("--half-spread", type=float, default=0.50)
    parser.add_argument("--skew", type=float, default=50.0)
    parser.add_argument("--gamma", type=float, default=0.1)
    parser.add_argument("--kappa", type=float, default=1.5)
    parser.add_argument("--min-spread", type=float, default=0.02)
    parser.add_argument("--latency-ms", type=int, default=0)
    parser.add_argument("--fill-ratio", type=float, default=1.0)
    parser.add_argument(
        "--maker-fee", "--maker-fee-rate", dest="maker_fee", type=float, default=0.0002,
        help="Assumed maker fee rate; legacy rate form",
    )
    parser.add_argument(
        "--taker-fee", "--taker-fee-rate", dest="taker_fee", type=float, default=0.0005,
        help="Assumed forced-flatten fee rate; legacy rate form",
    )
    parser.add_argument("--maker-fee-bps", type=float, default=None)
    parser.add_argument("--taker-fee-bps", type=float, default=None)
    parser.add_argument("--max-position", type=float, default=0.005)
    parser.add_argument("--max-drawdown", type=float, default=25.0)
    parser.add_argument("--no-flatten", action="store_true")
    parser.add_argument("--json-out", type=Path, help="Optional JSON report path")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    maker_fee_rate = (
        args.maker_fee_bps / 10_000.0
        if args.maker_fee_bps is not None
        else args.maker_fee
    )
    taker_fee_rate = (
        args.taker_fee_bps / 10_000.0
        if args.taker_fee_bps is not None
        else args.taker_fee
    )
    if maker_fee_rate < 0.0 or taker_fee_rate < 0.0:
        raise SystemExit("fee rates cannot be negative")
    if args.duration_ms is not None and args.duration_ms <= 0:
        raise SystemExit("--duration-ms must be positive")
    if args.end_ms is not None and args.start_ms is not None and args.end_ms < args.start_ms:
        raise SystemExit("--end-ms must be at or after --start-ms")
    if args.duration_ms is not None and args.end_ms is not None:
        raise SystemExit("use either --duration-ms or --end-ms, not both")
    depth_path, trades_path = _paths(args)
    for path in (depth_path, trades_path):
        if not path.is_file():
            raise SystemExit(f"missing capture: {path}")

    capture = HistoricalReplay.from_parquet(depth_path, trades_path)
    if args.duration_ms is not None:
        if args.start_ms is None:
            args.start_ms = int(capture.depth.event_time.min())
        args.end_ms = args.start_ms + args.duration_ms
    depth, trades, start_ms, end_ms = _slice_capture(
        capture.depth,
        capture.trades,
        start_ms=args.start_ms,
        end_ms=args.end_ms,
    )
    horizon_seconds = max((end_ms - start_ms) / 1000.0, 1.0)
    factory = _strategy_factory(args, horizon_seconds)

    results = {}
    for mode in args.modes:
        config = BacktestConfig(
            name=f"real_depth_{mode}",
            quote_interval_ms=100,
            flatten_at_end=not args.no_flatten,
            taker_fee_rate=taker_fee_rate,
            execution=ExecutionAssumptions(
                maker_fee_rate=maker_fee_rate,
                latency_ms=args.latency_ms,
                fill_ratio=args.fill_ratio,
                queue_model=mode,
            ),
            risk=MarketMakingRiskConfig(
                max_position=args.max_position,
                max_order_qty=args.qty,
                max_drawdown=args.max_drawdown,
            ),
        )
        result = HistoricalReplay(config, data_source=DATA_SOURCE_REAL_L2).run(
            depth,
            trades,
            factory,
        )
        results[mode] = result

    print("LOB-X REAL-DEPTH EXECUTION COMPARISON")
    print(f"data_source=real_l2  depth={depth_path}  trades={trades_path}")
    print(f"slice_ms={start_ms}..{end_ms}  depth_events={len(depth.groupby('event_time'))}  trades={len(trades)}")
    print("Bounds/mode table (shadow is the optimistic bound; conservative is the queue-aware bound)")
    print(
        f"{'mode':<22} {'quotes':>7} {'fills':>7} {'filled_qty':>12} "
        f"{'mean_queue':>12} {'depth_adv':>10} {'gross':>12} {'fees':>12} {'net':>12}"
    )
    print("-" * 123)
    for mode, result in results.items():
        counter = result.execution_counters[mode]
        label = {
            "shadow": "shadow (upper)",
            "queue_ahead": "queue_ahead",
            "conservative": "conservative (lower)",
        }[mode]
        print(
            f"{label:<22} {counter['quotes_placed']:>7} {counter['fills']:>7} "
            f"{counter['filled_qty']:>12.6f} {counter['mean_queue_ahead_at_placement']:>12.6f} "
            f"{counter['depth_advance_events']:>10} {counter['gross']:>12.6f} "
            f"{counter['fees']:>12.6f} {counter['net']:>12.6f}"
        )

    if args.json_out is not None:
        report = {
            "data_source": DATA_SOURCE_REAL_L2,
            "provenance": git_provenance(),
            "parameter_hash": parameter_hash(vars(args)),
            "depth_file": str(depth_path),
            "trades_file": str(trades_path),
            "slice": {"start_ms": start_ms, "end_ms": end_ms},
            "modes": {
                mode: {
                    "strategy": result.strategy,
                    "summary": result.summary,
                    "execution_counters": result.execution_counters,
                }
                for mode, result in results.items()
            },
        }
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"JSON report: {args.json_out}")


if __name__ == "__main__":
    main()
