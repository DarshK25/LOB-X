"""Run deterministic historical market-making backtests on a closed capture.

Examples
--------
python scripts/run_backtest.py --date 2026-08-14
python scripts/run_backtest.py --date 2026-08-14 --strategy as --scenario conservative

The output is a compact JSON summary plus fills/equity CSV audit trails under
``reports/backtests``. Results are paper-simulation evidence only; they are
not authorisation to send exchange orders.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

_BACKEND = Path(__file__).resolve().parents[1] / "backend" / "python"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from lobx.backtest.replay import BacktestConfig, HistoricalReplay
from lobx.execution.shadow_engine import ExecutionAssumptions
from lobx.risk import MarketMakingRiskConfig
from lobx.strategy.avellaneda import AvellanedaStoikovStrategy
from lobx.strategy.fixed_spread import FixedSpreadStrategy
from lobx.strategy.inventory_skew import InventorySkewStrategy
from lobx.strategy.no_trade import NoTradeStrategy


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _git_revision() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _scenario(name: str, args: argparse.Namespace) -> BacktestConfig:
    # These are intentionally assumptions, not claims about a venue fee tier.
    # Pass explicit values for the account/venue being evaluated.
    if name == "optimistic":
        execution = ExecutionAssumptions(
            maker_fee_rate=args.maker_fee, latency_ms=0, fill_ratio=1.0, queue_model="shadow"
        )
    elif name == "baseline":
        execution = ExecutionAssumptions(
            maker_fee_rate=args.maker_fee, latency_ms=100, fill_ratio=1.0, queue_model="shadow"
        )
    else:
        execution = ExecutionAssumptions(
            maker_fee_rate=args.maker_fee,
            latency_ms=250,
            fill_ratio=0.5,
            queue_model="queue_ahead",
        )
    return BacktestConfig(
        name=name,
        quote_interval_ms=args.quote_interval_ms,
        markout_horizon_ms=args.markout_ms,
        flatten_at_end=True,
        taker_fee_rate=args.taker_fee,
        execution=execution,
        risk=MarketMakingRiskConfig(
            max_position=args.max_position,
            max_order_qty=args.qty,
            max_drawdown=args.max_drawdown,
        ),
    )


def _strategy_factory(name: str, args: argparse.Namespace):
    if name == "no-trade":
        return NoTradeStrategy
    if name == "fixed":
        return lambda: FixedSpreadStrategy(half_spread=args.half_spread, qty=args.qty)
    if name == "inventory-skew":
        return lambda: InventorySkewStrategy(half_spread=args.half_spread, qty=args.qty, skew_usd_per_btc=args.skew)
    return lambda: AvellanedaStoikovStrategy(
        gamma=args.gamma,
        kappa=args.kappa,
        horizon_seconds=args.horizon,
        qty=args.qty,
        min_spread=args.min_spread,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("--date", required=True, help="UTC capture date, e.g. 2026-08-14")
    parser.add_argument("--data-dir", type=Path, default=Path("data/raw"))
    parser.add_argument("--output-dir", type=Path, default=Path("reports/backtests"))
    parser.add_argument("--strategy", choices=["no-trade", "fixed", "as", "inventory-skew", "all"], default="all")
    parser.add_argument("--scenario", choices=["optimistic", "baseline", "conservative", "all"], default="all")
    parser.add_argument("--qty", type=float, default=0.001)
    parser.add_argument("--half-spread", type=float, default=0.50)
    parser.add_argument("--gamma", type=float, default=0.1)
    parser.add_argument("--kappa", type=float, default=1.5,
                        help="AS placeholder scenario value; it is not data-calibrated")
    parser.add_argument("--horizon", type=float, default=300.0)
    parser.add_argument("--skew", type=float, default=50.0, help="Inventory skew in USD per BTC")
    parser.add_argument("--min-spread", type=float, default=0.02)
    parser.add_argument("--maker-fee", type=float, default=0.0002,
                        help="Assumed maker fee rate (0.0002 = 2 bps)")
    parser.add_argument("--taker-fee", type=float, default=0.0005,
                        help="Assumed forced-flatten fee rate (0.0005 = 5 bps)")
    parser.add_argument("--quote-interval-ms", type=int, default=100)
    parser.add_argument("--markout-ms", type=int, default=1000)
    parser.add_argument("--max-position", type=float, default=0.005)
    parser.add_argument("--max-drawdown", type=float, default=25.0)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    depth_path = args.data_dir / f"depth_{args.date}.parquet"
    trades_path = args.data_dir / f"trades_{args.date}.parquet"
    for path in (depth_path, trades_path):
        if not path.is_file():
            raise SystemExit(f"missing capture: {path}")

    # Load once. The underlying frames are reused by every strategy/scenario.
    capture = HistoricalReplay.from_parquet(depth_path, trades_path)
    strategies = ["no-trade", "fixed", "as", "inventory-skew"] if args.strategy == "all" else [args.strategy]
    scenarios = ["optimistic", "baseline", "conservative"] if args.scenario == "all" else [args.scenario]
    manifest_path = args.output_dir / f"manifest_{args.date}.json"
    previous_runs: list[dict] = []
    if manifest_path.is_file():
        try:
            previous_runs = json.loads(manifest_path.read_text(encoding="utf-8")).get("runs", [])
        except (json.JSONDecodeError, OSError):
            # A corrupted manifest must not invalidate the actual reports;
            # retain the new run and let source control/data hashes diagnose it.
            previous_runs = []
    manifest = {
        "depth_file": str(depth_path),
        "depth_sha256": _sha256(depth_path),
        "trades_file": str(trades_path),
        "trades_sha256": _sha256(trades_path),
        "git_revision": _git_revision(),
        "runs": previous_runs,
    }

    for strategy_name in strategies:
        for scenario_name in scenarios:
            replay = HistoricalReplay(_scenario(scenario_name, args))
            result = replay.run(capture.depth, capture.trades, _strategy_factory(strategy_name, args))
            report = result.write(args.output_dir)
            entry = {"strategy": strategy_name, "scenario": scenario_name, "report": str(report)}
            manifest["runs"] = [
                existing
                for existing in manifest["runs"]
                if (existing.get("strategy"), existing.get("scenario"))
                != (strategy_name, scenario_name)
            ]
            manifest["runs"].append(entry)
            s = result.summary
            print(
                f"{strategy_name:>5}  {scenario_name:<12} "
                f"net={s['net_pnl']:>9.4f}  maker_qty={s['maker_filled_qty']:>8.4f}  "
                f"maxDD={s['max_drawdown']:>8.4f}  halted={s['risk_halted']}"
            )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
