"""
validate_collection.py — post-collection data quality check.

Reads the Parquet files produced by run_collector.py and:
  1. Validates schema columns exist (trades + depth)
  2. Asserts row count > 0
  3. Computes time range (first → last event_time)
  4. Computes basic statistics (trade count, unique prices, buy/sell ratio)
  5. Writes a rich GitHub Step Summary (Markdown table) via GITHUB_STEP_SUMMARY
  6. Exits non-zero if validation fails — so the CI job goes red

Usage
-----
  python scripts/validate_collection.py --data-dir data/raw

In GitHub Actions the $GITHUB_STEP_SUMMARY env var is set automatically.
When run locally it prints to stdout instead.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# ── Path setup ────────────────────────────────────────────────────────────────
_BACKEND = Path(__file__).resolve().parents[1] / "backend" / "python"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


def _find_parquet(data_dir: Path, prefix: str) -> list[Path]:
    return sorted(data_dir.glob(f"{prefix}_*.parquet"))


def _ms_to_utc(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _write_summary(text: str) -> None:
    """Write to GitHub Step Summary if available, else print."""
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as f:
            f.write(text + "\n")
    else:
        print(text)


def validate(data_dir: Path) -> bool:
    """
    Run all validation checks. Returns True if everything is OK.
    """
    try:
        import pandas as pd
        import pyarrow.parquet as pq
    except ImportError as e:
        print(f"ERROR: Missing dependency: {e}")
        return False

    ok = True
    lines: list[str] = []

    lines.append("## 📊 LOB-X Market Data Collection Report")
    lines.append("")

    # ── Trades ────────────────────────────────────────────────────────────────
    trade_files = _find_parquet(data_dir, "trades")
    lines.append("### Trades")
    if not trade_files:
        lines.append("❌ **No trade Parquet files found.**")
        ok = False
    else:
        try:
            df_trades = pd.concat(
                [pd.read_parquet(f) for f in trade_files], ignore_index=True
            )
            required_cols = {"event_time", "trade_id", "price", "qty", "is_buyer_maker", "symbol"}
            missing = required_cols - set(df_trades.columns)
            if missing:
                lines.append(f"❌ **Missing columns:** `{missing}`")
                ok = False
            elif len(df_trades) == 0:
                lines.append("❌ **Zero rows in trade files.**")
                ok = False
            else:
                n = len(df_trades)
                t_first = _ms_to_utc(int(df_trades["event_time"].min()))
                t_last  = _ms_to_utc(int(df_trades["event_time"].max()))
                elapsed_s = (df_trades["event_time"].max() - df_trades["event_time"].min()) / 1000
                rate = n / elapsed_s if elapsed_s > 0 else 0
                buys  = (~df_trades["is_buyer_maker"]).sum()
                sells = df_trades["is_buyer_maker"].sum()
                buy_pct = buys / n * 100
                p_min = df_trades["price"].min()
                p_max = df_trades["price"].max()
                notional = (df_trades["price"] * df_trades["qty"]).sum()
                symbols = df_trades["symbol"].unique().tolist()
                file_sizes = sum(f.stat().st_size for f in trade_files)

                lines.append("")
                lines.append(f"| Metric | Value |")
                lines.append(f"|--------|-------|")
                lines.append(f"| ✅ Status | OK |")
                lines.append(f"| Files | {len(trade_files)} file(s) ({file_sizes/1024:.1f} KB) |")
                lines.append(f"| Symbol(s) | {', '.join(symbols)} |")
                lines.append(f"| Total trades | {n:,} |")
                lines.append(f"| First event | {t_first} |")
                lines.append(f"| Last event | {t_last} |")
                lines.append(f"| Elapsed | {elapsed_s/3600:.2f} hours |")
                lines.append(f"| Avg rate | {rate:.1f} trades/sec |")
                lines.append(f"| Buy ticks | {buys:,} ({buy_pct:.1f}%) |")
                lines.append(f"| Sell ticks | {sells:,} ({100-buy_pct:.1f}%) |")
                lines.append(f"| Price range | ${p_min:,.2f} – ${p_max:,.2f} |")
                lines.append(f"| Total notional | ${notional:,.0f} |")
        except Exception as e:
            lines.append(f"❌ **Error reading trade files:** `{e}`")
            ok = False

    lines.append("")

    # ── Depth ─────────────────────────────────────────────────────────────────
    depth_files = _find_parquet(data_dir, "depth")
    lines.append("### Depth")
    if not depth_files:
        lines.append("❌ **No depth Parquet files found.**")
        ok = False
    else:
        try:
            # Read only metadata for performance — depth files can be large
            total_rows = 0
            total_bytes = 0
            for f in depth_files:
                meta = pq.read_metadata(str(f))
                total_rows += meta.num_rows
                total_bytes += f.stat().st_size

            if total_rows == 0:
                lines.append("❌ **Zero rows in depth files.**")
                ok = False
            else:
                lines.append("")
                lines.append(f"| Metric | Value |")
                lines.append(f"|--------|-------|")
                lines.append(f"| ✅ Status | OK |")
                lines.append(f"| Files | {len(depth_files)} file(s) ({total_bytes/1024:.1f} KB) |")
                lines.append(f"| Total level-change rows | {total_rows:,} |")
                required_depth = {"event_time", "symbol", "side", "price", "qty"}
                pf = pq.read_table(str(depth_files[0]), columns=list(required_depth))
                missing_d = required_depth - set(pf.schema.names)
                if missing_d:
                    lines.append(f"| Schema | ❌ Missing: `{missing_d}` |")
                    ok = False
                else:
                    lines.append(f"| Schema | ✅ Valid |")
        except Exception as e:
            lines.append(f"❌ **Error reading depth files:** `{e}`")
            ok = False

    lines.append("")
    lines.append(f"---")
    lines.append(f"*Generated by `validate_collection.py` at "
                 f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}*")

    _write_summary("\n".join(lines))
    return ok


def main() -> int:
    p = argparse.ArgumentParser(description="Validate collected Parquet data")
    p.add_argument("--data-dir", default="data/raw", type=Path,
                   help="Directory containing Parquet files (default: data/raw)")
    args = p.parse_args()

    if not args.data_dir.exists():
        _write_summary(f"## ❌ LOB-X Validation Failed\n\nData directory `{args.data_dir}` does not exist.")
        print(f"ERROR: {args.data_dir} does not exist", file=sys.stderr)
        return 1

    success = validate(args.data_dir)
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
