"""Validate an actual collector log and its depth parquet continuity.

Includes a row-density sanity check to prevent fabricated or abnormally
thin datasets from being silently accepted as overnight evidence.
"""
import argparse
import re
from pathlib import Path
from datetime import datetime, timezone
import pandas as pd


def check_row_density(
    depth_path: Path,
    min_rows_per_hour: int = 5_000,
) -> bool:
    """Sanity check: a real overnight run at Binance's depth@100ms cadence
    produces far more rows than *min_rows_per_hour* per hour.  If density is
    implausibly low, the run is either broken or wasn't actually continuous —
    flag it instead of letting a thin dataset pass silently."""
    import pyarrow.parquet as pq

    meta = pq.read_metadata(str(depth_path))
    total_rows = meta.num_rows
    df = pd.read_parquet(depth_path, columns=["event_time"])
    hours = (df.event_time.max() - df.event_time.min()) / (1000 * 3600)
    rows_per_hour = total_rows / hours if hours > 0 else 0

    print(f"\nRow density : {rows_per_hour:,.0f} rows/hour over {hours:.2f} hours")
    if rows_per_hour < min_rows_per_hour:
        print(
            f"  FAIL — density is far below the ~{min_rows_per_hour:,}/hr floor "
            f"expected from a live depth@100ms feed.  This run is not usable "
            f"as overnight-stability evidence."
        )
        return False
    print("  Density plausible for a continuous live feed.")
    return True


def main():
    parser = argparse.ArgumentParser(description="Parse overnight data collector log.")
    parser.add_argument("date", type=str, nargs="?", help="Date string used in log filename if --log not provided")
    parser.add_argument("--log", type=str, help="Path to the log file to parse")
    parser.add_argument("--data-dir", type=str, default="data/raw")
    parser.add_argument("--gap-threshold", type=float, default=10.0)
    parser.add_argument("--min-rows-per-hour", type=int, default=5_000,
                        help="Minimum depth rows/hour to accept as real (default: 5000)")
    args = parser.parse_args()
    
    if args.log:
        log_path = Path(args.log)
    elif args.date:
        log_path = Path(f"logs/overnight_{args.date}.log")
    else:
        print("Error: Must provide either a date string or a --log path.")
        return
        
    if not log_path.exists():
        print(f"Error: Log file {log_path} not found.")
        return
        
    print(f"=== OVERNIGHT RUN CHECK: {log_path.name} ===")
    
    start_time = None
    end_time = None
    errors = 0
    warnings = []
    exceptions = []
    
    # Simple regex to extract ISO 8601 timestamps like "2026-08-05 23:00:01,123"
    time_pattern = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
    
    with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
        for line in f:
            match = time_pattern.match(line)
            if match:
                dt_str = match.group(1)
                try:
                    dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
                    if start_time is None:
                        start_time = dt
                    end_time = dt
                except ValueError:
                    pass
            
            if "ERROR" in line or "Exception" in line or "Traceback" in line:
                errors += 1
                if len(exceptions) < 5:
                    exceptions.append(line.strip())
            elif "WARNING" in line:
                warnings.append(line.strip())
                
    if start_time and end_time:
        duration = end_time - start_time
        hours = duration.total_seconds() / 3600
        print(f"Start time : {start_time}")
        print(f"End time   : {end_time}")
        print(f"Uptime     : {hours:.1f} hours ({duration})")
    else:
        print("Could not parse start/end timestamps from the log.")
        
    print(f"\nErrors     : {errors}")
    print(f"Warnings   : {len(warnings)}")
    if warnings:
        print("Warning lines:")
        for warning in warnings[:5]:
            print(f"  {warning}")
    
    if errors > 0:
        print("\nFirst few errors found:")
        for e in exceptions:
            print(f"  {e}")
            
    data_date = args.date
    depth_path = Path(args.data_dir) / f"depth_{data_date}.parquet" if data_date else None
    gaps_ok = False
    density_ok = False
    if depth_path and depth_path.exists():
        try:
            depth = pd.read_parquet(depth_path, columns=["event_time"]).sort_values("event_time")
            # Rows share a timestamp because one Binance event changes many levels.
            event_times = depth.event_time.drop_duplicates().to_numpy()
            gaps = (event_times[1:] - event_times[:-1]) / 1000.0
            big_gaps = gaps[gaps > args.gap_threshold]
            print(f"\nDepth rows : {len(depth):,}")
            print(f"Depth event coverage: {datetime.fromtimestamp(event_times[0] / 1000, tz=timezone.utc)} -> {datetime.fromtimestamp(event_times[-1] / 1000, tz=timezone.utc)}")
            print(f"Gaps > {args.gap_threshold:g}s : {len(big_gaps)}")
            if len(big_gaps):
                print(f"Largest gap : {big_gaps.max():.3f}s")
            else:
                print("No suspicious depth-event gaps.")
            gaps_ok = len(big_gaps) == 0
        except Exception as exc:
            print(f"\nDepth gap check failed: {type(exc).__name__}: {exc}")

        # Row-density guard: catch fabricated or abnormally thin datasets.
        try:
            density_ok = check_row_density(depth_path, args.min_rows_per_hour)
        except Exception as exc:
            print(f"\nRow density check failed: {type(exc).__name__}: {exc}")
    else:
        print(f"\nDepth gap check unavailable: {depth_path}")

    passed = errors == 0 and gaps_ok and density_ok
    print("\nVERDICT: " + ("PASS" if passed else "FAIL"))
    if not passed:
        reasons = []
        if errors > 0:
            reasons.append("errors in log")
        if not gaps_ok:
            reasons.append("missing/corrupt depth file or gaps above threshold")
        if not density_ok:
            reasons.append("row density too low for a real continuous feed")
        print("NOTES: " + "; ".join(reasons) + ".")

if __name__ == "__main__":
    main()
