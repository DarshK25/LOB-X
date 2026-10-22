#!/usr/bin/env python3
"""Run the full research protocol on the stored history windows.

    python scripts/run_research.py                 # every window in data/history
    python scripts/run_research.py --window calm   # one window
    python scripts/run_research.py --cross calm crash

Per window: in-sample calibration and gamma search, out-of-sample comparison
of all strategies, block-bootstrap CIs and stress tests. ``--cross A B``
calibrates and tunes on all of A and evaluates on all of B (regime shift).
Results go to results/research/*.json and a markdown summary.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "python"))

from lobx.backtest.data import list_history, load_history  # noqa: E402
from lobx.backtest.report import write_summary  # noqa: E402
from lobx.backtest.research import cross_regime, evaluate_window, save_result, walk_forward  # noqa: E402

OUT = ROOT / "results" / "research"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--window", action="append", help="history label (repeatable)")
    ap.add_argument("--cross", nargs=2, metavar=("TRAIN", "TEST"), help="cross-regime test")
    ap.add_argument("--fees", nargs="+", default=["vip0", "mm_rebate"])
    ap.add_argument("--nested", action="store_true",
                    help="separate calibration and gamma-selection periods inside the training half")
    ap.add_argument("--walk-forward", nargs=2, type=float, metavar=("TRAIN_H", "TEST_H"),
                    help="nested rolling folds (hours); saved under results/research/walk_forward")
    ap.add_argument("--step-hours", type=float, help="walk-forward step (default TEST_H)")
    ap.add_argument("--verify-data", action="store_true",
                    help="verify Parquet SHA-256 hashes against manifest before loading")
    ap.add_argument("--summary-only", action="store_true", help="rebuild the markdown summary")
    args = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    if not args.summary_only:
        labels = args.window or list_history()
        if not labels and not args.cross:
            print("No history found. Run: python scripts/fetch_history.py")
            return 1
        for label in labels:
            data = load_history(label, verify=args.verify_data)
            if args.walk_forward:
                train_h, test_h = args.walk_forward
                result = walk_forward(data, train_h * 3600, test_h * 3600, tuple(args.fees),
                                      None if args.step_hours is None else args.step_hours * 3600)
                target = OUT / "walk_forward"
            else:
                result = evaluate_window(data, tuple(args.fees), nested=args.nested)
                target = OUT
            print(f"saved {save_result(result, target)}")
        if args.cross:
            train, test = (load_history(x, verify=args.verify_data) for x in args.cross)
            result = cross_regime(train, test, tuple(args.fees))
            print(f"saved {save_result(result, OUT)}")
    path = write_summary(OUT)
    print(f"summary {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
