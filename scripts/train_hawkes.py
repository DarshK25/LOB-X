"""
train_hawkes.py — Calibrate Hawkes process parameters from trade data.

Usage:
    python scripts/train_hawkes.py --input data/trades.csv --T 3600
"""
import argparse
import csv
import sys
import os
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend", "python"))

import numpy as np
import pandas as pd
from lobx.models.hawkes.calibration import calibrate
from lobx.risk.hawkes import write_hawkes_artifact


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=str, help="CSV with 'timestamp' column")
    parser.add_argument("--parquet", type=Path, help="Real trade Parquet with event_time column")
    parser.add_argument("--T", type=float, default=None, help="Observation window in seconds")
    parser.add_argument("--starts", type=int, default=5)
    parser.add_argument(
        "--output", type=Path, default=None,
        help="Optional JSON artifact consumed by --hawkes-artifact in paper/backtest runners",
    )
    args = parser.parse_args()

    if (args.input is None) == (args.parquet is None):
        parser.error("provide exactly one of --input or --parquet")
    timestamps = []
    source = args.input if args.input is not None else args.parquet
    if args.parquet is not None:
        frame = pd.read_parquet(args.parquet, columns=["event_time"])
        raw = frame["event_time"].to_numpy(dtype=float) / 1000.0
        timestamps = raw.tolist()
    else:
        with open(args.input) as f:
            reader = csv.DictReader(f)
            for row in reader:
                timestamps.append(float(row["timestamp"]))

    events = np.array(sorted(timestamps))
    if args.T is None:
        args.T = float(events[-1] - events[0]) if len(events) > 1 else 1.0
    events = events - events[0] if len(events) else events
    print(f"Loaded {len(events)} events over T={args.T}s")

    params = calibrate(events, args.T, n_starts=args.starts)
    print(f"Calibrated Hawkes parameters:")
    print(f"  μ (baseline)  = {params['mu']:.6f}")
    print(f"  α (excitation) = {params['alpha']:.6f}")
    print(f"  β (decay)      = {params['beta']:.6f}")
    print(f"  Branching ratio (α/β) = {params['alpha']/params['beta']:.4f}")
    # Thresholds are estimated from the fitted process's observed event-time
    # intensity proxy. They are persisted as train-derived metadata; the risk
    # controller still requires explicit policy multipliers at runtime.
    train_events = events[: max(2, int(len(events) * 0.70))] if len(events) > 1 else events
    if len(train_events) > 1:
        gaps = np.diff(train_events)
        rates = 1.0 / np.maximum(gaps, 1e-9)
        thresholds = {
            "elevated_quantile": 0.90,
            "cascade_quantile": 0.99,
            "fit_fraction": 0.70,
            "elevated_intensity": float(np.quantile(rates, 0.90)),
            "cascade_intensity": float(np.quantile(rates, 0.99)),
        }
        print(f"  ELEVATED intensity q90 = {thresholds['elevated_intensity']:.6f}")
        print(f"  CASCADE intensity q99 = {thresholds['cascade_intensity']:.6f}")
    else:
        thresholds = {}
    if args.output is not None:
        output = write_hawkes_artifact(
            args.output,
            params,
            source_file=source,
            observation_window_seconds=args.T,
            event_count=len(events),
            thresholds=thresholds,
        )
        print(f"Wrote Hawkes calibration artifact: {output}")


if __name__ == "__main__":
    main()
