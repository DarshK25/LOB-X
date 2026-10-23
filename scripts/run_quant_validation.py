"""Run the chronological quantitative validation experiment.

The runner fits A-S parameters on the chronological train prefix and then
evaluates fixed-spread, canonical A-S, and no-trade benchmarks on a fresh OOS
replay.  It records exact source/frame hashes and the complete run
configuration beside the report.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

_BACKEND = Path(__file__).resolve().parents[1] / "backend" / "python"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from lobx.analytics.adverse_selection import analyze_adverse_selection
from lobx.analytics.calibration import sha256_file
from lobx.analytics.microprice import evaluate_microprice_predictive_power
from lobx.analytics.walk_forward import (
    WalkForwardConfig,
    chronological_split,
    hash_dataframe,
    run_walk_forward,
)


DEFAULT_PARQUET_PATH = Path(
    "data/parquet/BTCUSDT-futures-um-aggTrades-2026-09-01_to_2026-09-14.parquet"
)
OUT_DIR = Path("results/quant_validation")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("--parquet", type=Path, default=DEFAULT_PARQUET_PATH)
    parser.add_argument("--output-dir", type=Path, default=OUT_DIR)
    parser.add_argument("--max-trades", type=int, default=50_000)
    parser.add_argument("--train-fraction", type=float, default=0.70)
    # Keep the existing override names. None means fit that parameter on train.
    parser.add_argument("--kappa", type=float, default=None)
    parser.add_argument("--gamma", type=float, default=None)
    parser.add_argument("--qty", type=float, default=0.001)
    parser.add_argument("--inventory-limit", type=float, default=0.005)
    parser.add_argument("--spread-floor", "--min-spread", dest="spread_floor", type=float, default=0.02)
    parser.add_argument("--fixed-half-spread", type=float, default=0.50)
    parser.add_argument("--quote-interval-ms", type=int, default=100)
    parser.add_argument("--maker-fee-rate", "--maker-fee", dest="maker_fee_rate", type=float, default=0.0)
    parser.add_argument("--taker-fee-rate", "--taker-fee", dest="taker_fee_rate", type=float, default=0.0005)
    parser.add_argument("--maker-fee-bps", type=float, default=None)
    parser.add_argument("--taker-fee-bps", type=float, default=None)
    return parser.parse_args()


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value) if math.isfinite(float(value)) else None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (np.bool_,)):
        return bool(value)
    return value


def _scoreboard_for_report(scoreboard: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Keep the report compact while retaining all risk/P&L metrics."""
    fields = {
        "net_pnl",
        "gross_pnl",
        "realized_pnl",
        "fees_paid",
        "maker_fees",
        "taker_fees",
        "fees",
        "fills",
        "maker_fill_count",
        "turnover",
        "max_abs_position",
        "mean_abs_position",
        "max_drawdown",
        "sharpe_ratio",
        "sortino_ratio",
        "pnl_return_period_ms",
        "pnl_components",
        "pnl_reconciled",
        "traded_notional",
        "breakeven_fee_bps",
        "fee_sensitivity",
        "reconciliation_residual",
        "mean_1s_markout",
    }
    return {
        name: {key: metrics[key] for key in fields if key in metrics}
        for name, metrics in scoreboard.items()
    }


def main() -> None:
    args = parse_args()
    parquet_path = args.parquet
    out_dir = args.output_dir
    if not parquet_path.exists():
        raise FileNotFoundError(f"Parquet dataset not found at: {parquet_path.resolve()}")

    print("=" * 78)
    print("  LOB-X CHRONOLOGICAL TRAIN / FIT / OOS VALIDATION")
    print("=" * 78)
    out_dir.mkdir(parents=True, exist_ok=True)

    raw = pd.read_parquet(parquet_path)
    train, test = chronological_split(
        raw,
        train_fraction=args.train_fraction,
        max_trades=args.max_trades,
    )
    settings = WalkForwardConfig(
        train_fraction=args.train_fraction,
        quote_interval_ms=args.quote_interval_ms,
        qty=args.qty,
        inventory_limit=args.inventory_limit,
        fixed_half_spread=args.fixed_half_spread,
        spread_floor=args.spread_floor,
        gamma=args.gamma,
        kappa=args.kappa,
        maker_fee_rate=(
            args.maker_fee_bps / 10_000.0
            if args.maker_fee_bps is not None
            else args.maker_fee_rate
        ),
        taker_fee_rate=(
            args.taker_fee_bps / 10_000.0
            if args.taker_fee_bps is not None
            else args.taker_fee_rate
        ),
    )

    print(f"Loaded {len(train) + len(test):,} chronological trades from {parquet_path.name}")
    print(f"Train fit: {len(train):,} rows | Untouched OOS test: {len(test):,} rows")
    if int(train.event_time.iloc[-1]) > int(test.event_time.iloc[0]):
        raise RuntimeError("chronological split is not ordered")

    evaluation = run_walk_forward(
        raw,
        config=settings,
        max_trades=args.max_trades,
    )
    scoreboard = _scoreboard_for_report(evaluation.scoreboard)
    as_result = evaluation.results["as"]

    quote_records = [
        {
            "timestamp": event.get("timestamp"),
            "mid": event.get("market_mid"),
            "microprice_deviation": event.get("microprice_deviation", 0.0),
        }
        for event in as_result.events
        if event.get("event_type") == "QUOTE_CALCULATED"
        and event.get("market_mid") is not None
    ]
    micro_analysis = evaluate_microprice_predictive_power(
        quote_records,
        horizons_ms=(100, 500, 1_000, 5_000, 10_000),
    )
    adverse_selection = analyze_adverse_selection(as_result.markouts)

    data_hashes = {
        "source_file_sha256": sha256_file(parquet_path),
        "train_frame_sha256": hash_dataframe(train),
        "test_frame_sha256": hash_dataframe(test),
    }
    run_config = {
        "runner": "scripts/run_quant_validation.py",
        "arguments": vars(args),
        "walk_forward": evaluation.config,
        "data_hashes": data_hashes,
    }
    report = {
        "research_question": "Does calibrated canonical A-S improve OOS risk-adjusted P&L over fixed spread?",
        "data_hashes": data_hashes,
        "config": run_config,
        "dataset": {
            "source": str(parquet_path),
            "train_trades": evaluation.train_rows,
            "test_trades": evaluation.test_rows,
            "train_start": evaluation.train_start,
            "train_end": evaluation.train_end,
            "test_start": evaluation.test_start,
            "test_end": evaluation.test_end,
            "train_test_ratio": args.train_fraction,
            "chronological_and_disjoint": True,
        },
        "calibrated_parameters": evaluation.calibration,
        "scoreboard": scoreboard,
        "pnl_component_summary": {
            name: metrics.get("pnl_components", {})
            for name, metrics in evaluation.scoreboard.items()
        },
        "microprice_predictive_power": micro_analysis,
        "adverse_selection_study": adverse_selection,
        "limitations": [
            "aggTrades are used as a passive-fill proxy; true queue priority requires richer exchange data",
            "the Binance adapter generates deterministic two-sided depth from the trade tape",
            "risk ratios use P&L increments with an explicit observation-period annualization, not percentage returns on capital",
            "this validation uses the Python shadow replay and does not exercise the optional C++ matching extension",
        ],
    }
    report = _json_safe(report)
    (out_dir / "research_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (out_dir / "run_config.json").write_text(json.dumps(_json_safe(run_config), indent=2), encoding="utf-8")
    (out_dir / "data_hashes.json").write_text(json.dumps(data_hashes, indent=2), encoding="utf-8")

    markdown = [
        "# LOB-X Quantitative Validation",
        "",
        "## Chronological train / fit / test",
        f"- Dataset: `{parquet_path.name}`",
        f"- Train fit: **{evaluation.train_rows:,}** rows ({evaluation.train_start} → {evaluation.train_end})",
        f"- Untouched OOS test: **{evaluation.test_rows:,}** rows ({evaluation.test_start} → {evaluation.test_end})",
        f"- Source SHA-256: `{data_hashes['source_file_sha256']}`",
        "",
        "## OOS scoreboard",
        "| Strategy | Net P&L | Sharpe | Sortino | Fills | Max DD | Reconciled |",
        "| :--- | ---: | ---: | ---: | ---: | ---: | :---: |",
    ]
    for name, metrics in scoreboard.items():
        markdown.append(
            f"| **{name}** | {metrics.get('net_pnl', 0.0):.6f} | "
            f"{metrics.get('sharpe_ratio', 0.0):.4f} | {metrics.get('sortino_ratio', 0.0):.4f} | "
            f"{metrics.get('fills', metrics.get('maker_fill_count', 0))} | "
            f"{metrics.get('max_drawdown', 0.0):.6f} | {metrics.get('pnl_reconciled', False)} |"
        )
    markdown.extend(
        [
            "",
            "## Fitted train parameters",
            "```json",
            json.dumps(_json_safe(evaluation.calibration), indent=2),
            "```",
            "",
            "## Limitations",
            "- The Binance adapter fabricates a two-sided depth context from aggTrades; it is not a queue-priority replay.",
            "- Sharpe and Sortino are annualized from P&L increments at the reported replay observation period.",
            "- The optional C++ extension is not exercised by this Python validation path.",
        ]
    )
    (out_dir / "research_report.md").write_text("\n".join(markdown) + "\n", encoding="utf-8")

    print("\nOOS scoreboard")
    for name, metrics in scoreboard.items():
        print(
            f"  {name:<10} | net P&L {metrics.get('net_pnl', 0.0): .6f} | "
            f"Sharpe {metrics.get('sharpe_ratio', 0.0): .4f} | "
            f"Sortino {metrics.get('sortino_ratio', 0.0): .4f}"
        )
    print(f"\nReports saved under: {out_dir.resolve()}")


if __name__ == "__main__":
    main()
