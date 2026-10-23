"""
run_live_strategy.py — CLI entrypoint for Phase 2 live strategy loop.

Usage
─────
  # Fixed spread baseline (always run this first for comparison):
  python scripts/run_live_strategy.py --strategy fixed

  # Avellaneda-Stoikov (placeholder gamma/kappa until Phase 3 calibration):
  python scripts/run_live_strategy.py --strategy as

  # AS without C++ engine (pure Python fallback):
  python scripts/run_live_strategy.py --strategy as --no-engine-mirror

  # Custom parameters:
  python scripts/run_live_strategy.py --strategy as --gamma 0.05 --kappa 2.0 --horizon 600

  # Explicit data calibration artifact:
  python scripts/run_live_strategy.py --strategy as --calibration-artifact reports/calibration.json

What you'll see in the log
──────────────────────────
Every ~10 seconds (100 depth events):
  [FixedSpreadStrategy tick=100]  mid=67543.21  vol=0.000012
  quote: bid=67542.71 ask=67543.71  |  pos=0.000000  fills=0  rPnL=0.0000  uPnL=0.0000

When a shadow fill occurs (any real trade crosses our quote price):
  SHADOW FILL  BUY  0.001000 @ 67542.71  |  position=0.001000  realized_pnl=0.0000

After a fill with AS, the NEXT tick's bid/ask should visibly shift —
the reservation price compensates for inventory. Watch for this.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, "backend/python")

from lobx.market_data.collector import Collector
from lobx.analytics.calibration import CalibrationArtifact, load_calibration_artifact
from lobx.strategy.fixed_spread import FixedSpreadStrategy
from lobx.strategy.avellaneda import AvellanedaStoikovStrategy
from lobx.live.quote_loop import StrategyRunner
from lobx.execution.shadow_engine import ExecutionAssumptions
from lobx.execution.unwind import UnwindConfig
from lobx.risk import (
    HawkesParameters,
    HawkesRiskConfig,
    MarketMakingRiskConfig,
    load_hawkes_parameters,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)


def build_strategy(
    args: argparse.Namespace,
    calibration: CalibrationArtifact | None = None,
):
    if args.strategy == "fixed":
        return FixedSpreadStrategy(half_spread=args.half_spread, qty=args.qty)
    return AvellanedaStoikovStrategy(
        gamma=args.gamma,
        kappa=calibration.kappa if calibration is not None else args.kappa,
        horizon_seconds=args.horizon,
        qty=args.qty,
        min_spread=args.min_spread,
        sigma_override=calibration.sigma if calibration is not None else None,
    )


async def main(args: argparse.Namespace) -> None:
    calibration = (
        load_calibration_artifact(
            args.calibration_artifact,
            require_usable=not args.allow_uncalibrated,
        )
        if args.calibration_artifact is not None
        else None
    )
    collector = Collector(
        symbol=args.symbol,
        enable_engine_mirror=not args.no_engine_mirror,
        data_dir=args.data_dir,
        enable_storage=args.record_market_data,
    )
    strategy = build_strategy(args, calibration)
    hawkes_parameters = (
        load_hawkes_parameters(args.hawkes_artifact)
        if args.hawkes_artifact is not None
        else HawkesParameters(args.hawkes_mu, args.hawkes_alpha, args.hawkes_beta)
    )
    hawkes_enabled = args.hawkes_risk or args.hawkes_artifact is not None
    runner = StrategyRunner(
        collector,
        strategy,
        execution_assumptions=ExecutionAssumptions(
            maker_fee_rate=args.maker_fee,
            latency_ms=args.latency_ms,
            fill_ratio=args.fill_ratio,
            queue_model=args.queue_model,
            max_quote_age_ms=args.max_quote_age_ms,
        ),
        risk_config=MarketMakingRiskConfig(
            max_position=args.max_position,
            max_order_qty=args.qty,
            max_drawdown=args.max_drawdown,
            hawkes=(
                HawkesRiskConfig(
                    parameters=hawkes_parameters,
                    elevated_ratio=args.hawkes_elevated_ratio,
                    cascade_ratio=args.hawkes_cascade_ratio,
                    elevated_spread_multiplier=args.hawkes_spread_multiplier,
                    cooldown_ms=args.hawkes_cooldown_ms,
                )
                if hawkes_enabled else None
            ),
            unwind=(
                UnwindConfig(
                    horizon_ms=args.unwind_horizon_ms,
                    interval_ms=args.unwind_interval_ms,
                )
                if args.unwind_plan else None
            ),
        ),
    )

    book_source = "C++ engine" if collector.engine_mirror is not None else "Python reference book"
    print(f"\n  Strategy : {strategy.name()}")
    print(f"  Symbol   : {args.symbol}")
    print(f"  Book     : {book_source}")
    print(f"  Qty      : {args.qty} BTC per side")
    if calibration is not None:
        print(f"  Calibration: {args.calibration_artifact} (sigma={calibration.sigma:g}, kappa={calibration.kappa:g})")
    else:
        print("  Calibration: placeholder defaults")
    print(f"  Press Ctrl+C to stop.\n")

    try:
        await collector.run()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        collector.close()
        s = runner.summary()
        if args.events_output:
            output = Path(args.events_output)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps({
                "summary": s,
                "events": runner.shadow_engine.events,
                "fills": runner.shadow_engine.fills,
            }, indent=2), encoding="utf-8")
            print(f"  Audit events   : {output}")
        print("\n" + "=" * 55)
        print("  SESSION SUMMARY")
        print("=" * 55)
        print(f"  Strategy      : {s['strategy']}")
        print(f"  Depth ticks   : {s['ticks']:,}")
        print(f"  Shadow fills  : {s['fills']}")
        print(f"  Final position: {s['position']:.6f} BTC")
        if s['position'] != 0.0:
            print(f"  Avg entry     : {s['avg_entry']:.2f} USD")
        print(f"  Realized PnL  : {s['realized_pnl']:.4f} USD")
        print(f"  Unrealized PnL: {s['unrealized_pnl']:.4f} USD")
        print(f"  Fees          : {s['fees_paid']:.4f} USD")
        print(f"  Total PnL     : {s['total_pnl']:.4f} USD")
        print(f"  Max inventory : {s['max_inventory']:.6f} BTC")
        print(f"  Avg inventory : {s['average_inventory']:.6f} BTC")
        print(f"  Long time     : {s['time_spent_long_ms'] / 1000:.1f} s")
        print(f"  Short time    : {s['time_spent_short_ms'] / 1000:.1f} s")
        print(f"  Risk halted   : {s['risk_halted']}" +
              (f" ({s['risk_halt_reason']})" if s['risk_halt_reason'] else ""))
        print("=" * 55)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="LOB-X Phase 2 — live market-making strategy loop",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--symbol",   default="BTCUSDT", help="Binance trading pair")
    p.add_argument("--strategy", choices=["fixed", "as"], default="fixed",
                   help="'fixed' = fixed spread baseline; 'as' = Avellaneda-Stoikov")
    p.add_argument("--qty",         type=float, default=0.001, help="Quote size (BTC)")
    # Fixed spread params
    p.add_argument("--half-spread", type=float, default=0.5,
                   help="[fixed] Half-spread in USD")
    # AS params
    p.add_argument("--gamma",       type=float, default=0.1,
                   help="[as] Risk-aversion coefficient (placeholder until Phase 3 calibration)")
    p.add_argument("--kappa",       type=float, default=1.5,
                   help="[as] Order-flow intensity (placeholder until Phase 3 calibration)")
    p.add_argument("--horizon",     type=float, default=300.0,
                   help="[as] Session horizon in seconds")
    p.add_argument("--min-spread",  type=float, default=0.02,
                   help="[as] Minimum total spread enforced (prevents bid>=ask at zero vol)")
    # Execution and risk assumptions are explicit even in paper trading.
    p.add_argument("--maker-fee", type=float, default=0.0002,
                   help="Assumed maker fee rate (0.0002 = 2 bps)")
    p.add_argument("--latency-ms", type=int, default=100,
                   help="Quote activation latency used by the paper broker")
    p.add_argument("--max-quote-age-ms", type=int, default=None,
                   help="Cancel quotes before matching when older than this age")
    p.add_argument("--fill-ratio", type=float, default=1.0,
                   help="Maximum fraction of each eligible trade available to our quote")
    p.add_argument(
        "--queue-model",
        choices=["shadow", "queue_ahead", "conservative"],
        default="shadow",
        help=(
            "Paper fill model; shadow is optimistic, queue_ahead consumes "
            "displayed queue, conservative also advances from L2 reductions"
        ),
    )
    p.add_argument("--max-position", type=float, default=0.005,
                   help="Hard absolute BTC inventory limit")
    p.add_argument("--max-drawdown", type=float, default=25.0,
                   help="Hard paper-session drawdown limit in USDT")
    p.add_argument(
        "--hawkes-risk", action="store_true",
        help="Enable explicit Hawkes ELEVATED/CASCADE quote controls (opt-in)",
    )
    p.add_argument(
        "--hawkes-artifact", type=Path, default=None,
        help="Load calibrated Hawkes mu/alpha/beta JSON and enable Hawkes risk controls",
    )
    p.add_argument("--hawkes-mu", type=float, default=1.0)
    p.add_argument("--hawkes-alpha", type=float, default=0.8)
    p.add_argument("--hawkes-beta", type=float, default=2.0)
    p.add_argument("--hawkes-elevated-ratio", type=float, default=2.0)
    p.add_argument("--hawkes-cascade-ratio", type=float, default=5.0)
    p.add_argument("--hawkes-spread-multiplier", type=float, default=1.5)
    p.add_argument("--hawkes-cooldown-ms", type=int, default=1_000)
    p.add_argument(
        "--unwind-plan", action="store_true",
         help="Generate an Almgren-Chriss-style reduce-only unwind plan; never submits orders",
    )
    p.add_argument("--unwind-horizon-ms", type=int, default=30_000)
    p.add_argument("--unwind-interval-ms", type=int, default=1_000)
    p.add_argument(
        "--calibration-artifact", type=Path, default=None,
        help="Explicit v1 calibration artifact to use for AS sigma/kappa; no artifact keeps placeholder defaults",
    )
    p.add_argument(
        "--allow-uncalibrated", action="store_true",
        help="Allow an invalid calibration artifact and label the session uncalibrated",
    )
    p.add_argument("--no-engine-mirror", action="store_true",
                   help="Use Python reference book instead of C++ engine")
    p.add_argument("--data-dir", default="data/raw_strategy",
                   help="Market-data capture directory when recording is enabled")
    p.add_argument("--record-market-data", action="store_true",
                   help="Persist depth/trade Parquet files during this run")
    p.add_argument("--events-output", default=None,
                   help="Write strategy summary, fills, and events to this JSON file on exit")
    return p.parse_args()


if __name__ == "__main__":
    asyncio.run(main(parse_args()))
