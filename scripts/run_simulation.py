"""
run_simulation.py — CLI entry point for running the full simulation.

Usage:
    python scripts/run_simulation.py --ticks 5000 --mid 10000 --seed 42
"""
import argparse
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend", "python"))

import lobx_cpp
from lobx.simulation.simulator import Simulator
from lobx.simulation.random_trader import RandomTrader
from lobx.simulation.momentum_trader import MomentumTrader
from lobx.simulation.mean_reversion import MeanReversionTrader
from lobx.simulation.naive_market_maker import NaiveMarketMaker
from lobx.models.avellaneda.market_maker import AvellanedaStoikovMM


def main() -> None:
    parser = argparse.ArgumentParser(description="Run LOB-X simulation")
    parser.add_argument("--ticks",  type=int, default=1000)
    parser.add_argument("--mid",    type=int, default=10_000)
    parser.add_argument("--seed",   type=int, default=42)
    args = parser.parse_args()

    sim = Simulator(mid_price=args.mid)

    tid = 1
    for _ in range(5):
        sim.register(RandomTrader(sim.book, args.mid, tid, seed=args.seed)); tid += 50
    sim.register(MomentumTrader(sim.book, args.mid, tid, seed=args.seed)); tid += 50
    sim.register(MeanReversionTrader(sim.book, args.mid, tid)); tid += 50
    sim.register(NaiveMarketMaker(sim.book, args.mid, tid)); tid += 50
    sim.register(AvellanedaStoikovMM(sim.book, tid, session_ticks=args.ticks)); tid += 50

    result = sim.run(args.ticks)
    print(f"Ticks:        {result.ticks}")
    print(f"Total trades: {result.total_trades}")
    print(f"Total volume: {result.total_volume}")
    best_bid = sim.book.best_bid()
    best_ask = sim.book.best_ask()
    print(f"Final book:   bid={best_bid}  ask={best_ask}")


if __name__ == "__main__":
    main()
