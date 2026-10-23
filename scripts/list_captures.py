"""Inventory captured sessions and identify real versus synthetic depth."""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


def _duration(frame: pd.DataFrame) -> float:
    if frame.empty:
        return 0.0
    return (int(frame.event_time.max()) - int(frame.event_time.min())) / 1000.0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, action="append", default=None)
    args = parser.parse_args()
    directories = args.data_dir or [Path("data/raw"), Path("data/raw_watch"), Path("data/fresh_calibration")]
    print("date | duration_s | depth_rows | trade_rows | data_source | depth_file | trades_file")
    print("-" * 140)
    for directory in directories:
        for depth_path in sorted(directory.glob("depth_*.parquet")):
            date = depth_path.stem.removeprefix("depth_")
            trades_path = directory / f"trades_{date}.parquet"
            if not trades_path.is_file():
                continue
            try:
                depth = pd.read_parquet(depth_path, columns=["event_time", "side", "price", "qty"])
                trades = pd.read_parquet(trades_path, columns=["event_time"])
            except Exception as exc:
                print(f"{date} | READ_FAIL | {type(exc).__name__}: {exc}")
                continue
            print(
                f"{date} | {_duration(depth):.3f} | {len(depth)} | {len(trades)} | "
                f"real_l2 | {depth_path} | {trades_path}"
            )
    print("synthetic_from_trades | not listed as a capture: generated only by from_binance_parquet/from_binance_dataframe")


if __name__ == "__main__":
    main()
