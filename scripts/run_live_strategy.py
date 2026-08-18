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
import logging
import sys

sys.path.insert(0, "backend/python")

from lobx.market_data.collector import Collector
from lobx.strategy.fixed_spread import FixedSpreadStrategy
from lobx.strategy.avellaneda import AvellanedaStoikovStrategy
from lobx.live.quote_loop import StrategyRunner
from lobx.execution.shadow_engine import ExecutionAssumptions
from lobx.risk import MarketMakingRiskConfig

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)


def build_strategy(args: argparse.Namespace):
    if args.strategy == "fixed":
        return FixedSpreadStrategy(half_spread=args.half_spread, qty=args.qty)
    return AvellanedaStoikovStrategy(
        gamma=args.gamma,
        kappa=args.kappa,
        horizon_seconds=args.horizon,
        qty=args.qty,
        min_spread=args.min_spread,
    )


async def main(args: argparse.Namespace) -> None:
    collector = Collector(
        symbol=args.symbol,
        enable_engine_mirror=not args.no_engine_mirror,
        data_dir="data/raw_strategy",
    )
    strategy = build_strategy(args)
    runner = StrategyRunner(
        collector,
        strategy,
        execution_assumptions=ExecutionAssumptions(
            maker_fee_rate=args.maker_fee,
            latency_ms=args.latency_ms,
            fill_ratio=args.fill_ratio,
            queue_model=args.queue_model,
        ),
        risk_config=MarketMakingRiskConfig(
            max_position=args.max_position,
            max_order_qty=args.qty,
            max_drawdown=args.max_drawdown,
        ),
    )

    book_source = "C++ engine" if collector.engine_mirror is not None else "Python reference book"
    print(f"\n  Strategy : {strategy.name()}")
    print(f"  Symbol   : {args.symbol}")
    print(f"  Book     : {book_source}")
    print(f"  Qty      : {args.qty} BTC per side")
    print(f"  Press Ctrl+C to stop.\n")

    try:
        await collector.run()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        collector.close()
        s = runner.summary()
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
    p.add_argument("--fill-ratio", type=float, default=1.0,
                   help="Maximum fraction of each eligible trade available to our quote")
    p.add_argument("--queue-model", choices=["shadow", "queue_ahead"], default="shadow",
                   help="Paper fill model; queue_ahead is a stricter L2 approximation")
    p.add_argument("--max-position", type=float, default=0.005,
                   help="Hard absolute BTC inventory limit")
    p.add_argument("--max-drawdown", type=float, default=25.0,
                   help="Hard paper-session drawdown limit in USDT")
    p.add_argument("--no-engine-mirror", action="store_true",
                   help="Use Python reference book instead of C++ engine")
    return p.parse_args()


if __name__ == "__main__":
    asyncio.run(main(parse_args()))
