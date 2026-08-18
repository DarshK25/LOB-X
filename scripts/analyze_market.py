"""Run the data-derived Phase 3 market calibration pipeline.

No synthetic observations are used here.  Kappa is fitted only when the
collected depth and trade files contain enough overlapping, real observations.
"""
from __future__ import annotations

import sys
import argparse
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend" / "python"))
from lobx.analytics.calibration import empirical_fill_curve, fit_kappa_from_fill_curve


def readable_parquet(paths: list[Path]) -> Path:
    for path in sorted(paths, reverse=True):
        try:
            pd.read_parquet(path, columns=["event_time"])
            return path
        except Exception as exc:
            print(f"Skipping unreadable file {path.name}: {type(exc).__name__}: {exc}")
    raise FileNotFoundError("no readable trade parquet files found")


def recovery_evidence(raw_file: Path, clean_file: Path | None) -> dict:
    """Report an honest baseline; a failed full read is not zero rows."""
    metadata_rows = None
    full_readable = False
    try:
        raw = pd.read_parquet(raw_file)
        metadata_rows = len(raw)
        full_readable = True
    except Exception as exc:
        print(f"Raw full read failed: {type(exc).__name__}: {exc}")
        try:
            metadata_rows = pq.ParquetFile(raw_file).metadata.num_rows
            print("Raw row count recovered from Parquet metadata only.")
        except Exception as metadata_exc:
            print(f"Raw metadata unreadable: {type(metadata_exc).__name__}: {metadata_exc}")
    clean_rows = len(pd.read_parquet(clean_file)) if clean_file else None
    print("\n=== Parquet Recovery Evidence ===")
    print(f"Raw file   : {raw_file}")
    print(f"  Fully readable : {full_readable}")
    print(f"  Row count      : {metadata_rows if metadata_rows is not None else 'UNKNOWN'}")
    if clean_file:
        print(f"Clean file : {clean_file} -> {clean_rows:,} salvaged rows")
    if metadata_rows is not None and clean_rows is not None:
        difference = metadata_rows - clean_rows
        print(f"Not salvaged: {difference:,} rows ({difference / metadata_rows * 100:.3f}%)")
    elif clean_rows is not None:
        print("Not salvaged: UNKNOWN — original row count is unrecoverable.")
    return {"raw_rows": metadata_rows, "raw_fully_readable": full_readable, "clean_rows": clean_rows}


def calibrate_sigma(trades: pd.DataFrame) -> dict:
    ordered = trades.sort_values("event_time", kind="stable")
    dt = np.diff(ordered.event_time.to_numpy()) / 1000.0
    positive_dt = dt[dt > 0]
    median_dt = float(np.median(positive_dt)) if len(positive_dt) else 0.001
    returns = np.diff(np.log(ordered.price.to_numpy(dtype=float)))
    # Preserve zero returns: they are observed market data, not missing data.
    sigma_tick = float(np.std(returns)) if len(returns) else 0.0
    sigma_second = sigma_tick / np.sqrt(median_dt) if median_dt > 0 else float("nan")
    print("\n=== Sigma Calibration — Real Data, Units Explicit ===")
    print(f"Median inter-trade interval : {median_dt * 1000:.3f} ms ({median_dt:.6f} sec)")
    print(f"sigma per observed trade step (log-return): {sigma_tick:.8f}")
    print(f"sigma per sqrt(second)                    : {sigma_second:.8f}")
    return {"sigma_per_trade_step": sigma_tick, "sigma_per_sqrt_second": sigma_second, "median_dt_sec": median_dt}


def run_analysis(data_dirs: list[Path], sample_interval_ms: int = 1_000) -> dict:
    all_curves = []
    total_depth = 0
    total_trades = 0
    tick_size = None
    sigma = None
    trades_file_list = []
    
    print("=" * 72)
    print("LOB-X Offline Market Analytics & Calibration (real collected data)")
    print("=" * 72)

    for data_dir in data_dirs:
        if not data_dir.exists():
            continue
        clean_files = list(data_dir.glob("trades_*_clean.parquet"))
        regular_files = [p for p in data_dir.glob("trades_*.parquet") if "_clean" not in p.name]
        try:
            trades_file = readable_parquet(clean_files + regular_files)
        except FileNotFoundError:
            continue
            
        date = trades_file.stem.split("_")[1]
        depth_file = data_dir / f"depth_{date}.parquet"
        if not depth_file.exists():
            continue

        print(f"Reading {data_dir.name}: {trades_file.name}, {depth_file.name}")
        trades_file_list.append(trades_file)
        
        raw_file = data_dir / trades_file.name.replace("_clean", "")
        if raw_file.exists():
            recovery_evidence(raw_file, trades_file if "_clean" in trades_file.name else None)
            
        trades = pd.read_parquet(trades_file).sort_values("event_time", kind="stable")
        depth = pd.read_parquet(depth_file).sort_values("event_time", kind="stable")
        
        total_trades += len(trades)
        total_depth += len(depth)
        
        sigma = calibrate_sigma(trades)  # Will keep the last one or we could average
        
        curve, ts = empirical_fill_curve(depth, trades, sample_interval_ms=sample_interval_ms)
        if tick_size is None:
            tick_size = ts
        all_curves.append(curve)

    if not all_curves:
        raise ValueError("No readable datasets found in provided directories.")

    # Pool the curves
    pooled_curve = pd.concat(all_curves).groupby("distance_usd", as_index=False).sum()
    pooled_curve["fill_rate_per_quote_second"] = pooled_curve["fill_count"] / pooled_curve["exposure_quote_seconds"]
    print("\n=== Kappa Calibration — Real Quote-Exposure Data ===")
    print(f"Source rows (pooled): {total_depth:,} depth updates, {total_trades:,} trades")
    print("\nBucket Diagnostics (distance in USD, 0.001 sub-tick resolution):")
    print(pooled_curve[["distance_usd", "fill_count", "fill_rate_per_quote_second"]].to_string(index=False))
    
    pooled_curve.to_csv("reports/fill_curve_real_data.csv", index=False)
    
    try:
        fit = fit_kappa_from_fill_curve(pooled_curve)
    except ValueError as e:
        print(f"\n[!] KAPPA FIT FAILED: {e}")
        print("Gamma cannot be derived. Exiting.")
        raise SystemExit(1)
    print(f"Tick size inferred: {tick_size:g} USD; fit distance unit: USD (0.001 sub-tick resolution)")
    print(f"kappa      : {fit['kappa']:.8f} per USD")
    print(f"R-squared  : {fit['r_squared']:.6f}")
    print(f"p-value    : {fit['p_value']:.6g}")
    print(f"n points   : {fit['n_points']}")
    print("Curve saved: reports/fill_curve_real_data.csv")

    max_inventory = 10.0
    gamma = 1.0 / (max_inventory * fit["kappa"])
    print("\n=== Gamma — Derived from Kappa ===")
    print(f"Assumed max inventory : {max_inventory:.1f} BTC")
    print(f"Kappa used (real fit) : {fit['kappa']:.8f} per USD")
    print(f"Derived gamma         : {gamma:.8f}")
    
    if gamma < 0.01 or gamma > 2.0:
        print(f"\n[WARNING] Derived gamma {gamma:.3f} falls far outside the [0.01, 0.9] range")
        print("identified by Falces Marín et al. (2022) for BTC-USD. The calibration may be unstable.")
        
    return {"trades_files": [str(p) for p in trades_file_list], "sigma": sigma, "kappa": fit, "gamma": gamma, "max_inventory": max_inventory, "tick_size": tick_size}


def main() -> None:
    parser = argparse.ArgumentParser(description="Calibrate from collected market data")
    parser.add_argument("--data-dir", type=Path, default=Path("data/raw"))
    parser.add_argument("--pool", action="store_true", help="Pool data from multiple known directories")
    parser.add_argument(
        "--sample-interval", type=int, default=1_000, metavar="MS",
        help="Depth snapshot interval in ms for fill-curve exposure counting (default: 1000). "
             "Increase to 10000 for large overnight files to reduce computation time while "
             "still producing a statistically valid kappa fit.",
    )
    args = parser.parse_args()
    
    dirs = [args.data_dir]
    if args.pool:
        dirs = [Path("data/raw"), Path("data/fresh_calibration"), Path("data/raw_watch")]
        
    try:
        run_analysis(dirs, sample_interval_ms=args.sample_interval)
    except Exception as exc:
        print(f"CALIBRATION FAILED: {type(exc).__name__}: {exc}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
