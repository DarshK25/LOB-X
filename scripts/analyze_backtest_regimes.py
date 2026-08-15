"""Attribute a completed backtest's fills to volatility/liquidity regimes."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

import pandas as pd

_BACKEND = Path(__file__).resolve().parents[1] / "backend" / "python"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from lobx.analytics.regimes import (
    RegimeConfig,
    attribute_fills_to_regimes,
    sample_market_states,
    summarise_regimes,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("--depth", type=Path, required=True)
    parser.add_argument("--fills", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sample-ms", type=int, default=1000)
    parser.add_argument("--vol-window", type=int, default=60)
    parser.add_argument("--markout-ms", type=int, default=1000)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = RegimeConfig(args.sample_ms, args.vol_window, args.markout_ms)
    depth = pd.read_parquet(args.depth, columns=["event_time", "side", "price", "qty"])
    fills = pd.read_csv(args.fills)
    states = sample_market_states(depth, config)
    attributed = attribute_fills_to_regimes(fills, states, config)
    summary = summarise_regimes(attributed)
    args.output.mkdir(parents=True, exist_ok=True)
    states.to_csv(args.output / "market_states.csv", index=False)
    attributed.to_csv(args.output / "attributed_fills.csv", index=False)
    summary.to_csv(args.output / "regime_summary.csv", index=False)
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
