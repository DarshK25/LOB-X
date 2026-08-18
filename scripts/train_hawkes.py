"""
train_hawkes.py — Calibrate Hawkes process parameters from trade data.

Usage:
    python scripts/train_hawkes.py --input data/trades.csv --T 3600
"""
import argparse
import csv
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend", "python"))

import numpy as np
from lobx.models.hawkes.calibration import calibrate


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=str, required=True, help="CSV with 'timestamp' column")
    parser.add_argument("--T",     type=float, required=True, help="Observation window in seconds")
    parser.add_argument("--starts", type=int, default=5)
    args = parser.parse_args()

    timestamps = []
    with open(args.input) as f:
        reader = csv.DictReader(f)
        for row in reader:
            timestamps.append(float(row["timestamp"]))

    events = np.array(sorted(timestamps))
    print(f"Loaded {len(events)} events over T={args.T}s")

    params = calibrate(events, args.T, n_starts=args.starts)
    print(f"Calibrated Hawkes parameters:")
    print(f"  μ (baseline)  = {params['mu']:.6f}")
    print(f"  α (excitation) = {params['alpha']:.6f}")
    print(f"  β (decay)      = {params['beta']:.6f}")
    print(f"  Branching ratio (α/β) = {params['alpha']/params['beta']:.4f}")


if __name__ == "__main__":
    main()
