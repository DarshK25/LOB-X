"""Run the reproducible multi-session strategy comparison.

This is the Phase 8 evidence runner.  It evaluates fixed spread, canonical AS,
and no-trade on every matching depth/trade capture in a directory, then reports
session-level bootstrap intervals.  It never treats fills from one session as
independent samples from another and it writes failed sessions explicitly.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd

_BACKEND = Path(__file__).resolve().parents[1] / "backend" / "python"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from lobx.analytics.bootstrap import bootstrap_mean_ci
from lobx.analytics.calibration import CalibrationArtifact, load_calibration_artifact
from lobx.analytics.pnl_summary import summarize_fills
from lobx.analytics.provenance import git_provenance, parameter_hash
from lobx.backtest.replay import BacktestConfig, HistoricalReplay
from lobx.execution.shadow_engine import ExecutionAssumptions
from lobx.risk import MarketMakingRiskConfig
from lobx.strategy.avellaneda import AvellanedaStoikovStrategy
from lobx.strategy.fixed_spread import FixedSpreadStrategy
from lobx.strategy.no_trade import NoTradeStrategy


def _config(args: argparse.Namespace) -> BacktestConfig:
    maker_fee_rate = (
        getattr(args, "maker_fee_bps", None) / 10_000.0
        if getattr(args, "maker_fee_bps", None) is not None
        else args.maker_fee
    )
    taker_fee_rate = (
        getattr(args, "taker_fee_bps", None) / 10_000.0
        if getattr(args, "taker_fee_bps", None) is not None
        else args.taker_fee
    )
    if args.scenario == "optimistic":
        execution = ExecutionAssumptions(
            maker_fee_rate=maker_fee_rate,
            latency_ms=0,
            fill_ratio=1.0,
            queue_model="shadow",
        )
    elif args.scenario == "baseline":
        execution = ExecutionAssumptions(
            maker_fee_rate=maker_fee_rate,
            latency_ms=100,
            fill_ratio=1.0,
            queue_model="shadow",
        )
    else:
        execution = ExecutionAssumptions(
            maker_fee_rate=maker_fee_rate,
            latency_ms=250,
            fill_ratio=0.5,
            queue_model="conservative",
        )
    return BacktestConfig(
        name=f"research_{args.scenario}",
        quote_interval_ms=args.quote_interval_ms,
        markout_horizon_ms=args.markout_ms,
        flatten_at_end=True,
        taker_fee_rate=taker_fee_rate,
        execution=execution,
        risk=MarketMakingRiskConfig(
            max_position=args.max_position,
            max_order_qty=args.qty,
            max_drawdown=args.max_drawdown,
        ),
    )


def _factories(args: argparse.Namespace, calibration: CalibrationArtifact | None) -> dict[str, Any]:
    return {
        "fixed": lambda: FixedSpreadStrategy(half_spread=args.half_spread, qty=args.qty),
        "as": lambda: AvellanedaStoikovStrategy(
            gamma=args.gamma,
            kappa=calibration.kappa if calibration is not None else args.kappa,
            qty=args.qty,
            horizon_seconds=args.horizon,
            min_spread=args.min_spread,
            sigma_override=calibration.sigma if calibration is not None else None,
        ),
        "no_trade": NoTradeStrategy,
    }


def _summary_row(date: str, strategy: str, result, args: argparse.Namespace) -> dict[str, Any]:
    summary = result.summary
    pnl = summarize_fills(
        result.fills,
        final_mid=float(summary.get("final_mid", 0.0)),
        maker_bps=args.maker_fee_bps if args.maker_fee_bps is not None else args.maker_fee * 10_000.0,
        taker_bps=args.taker_fee_bps if args.taker_fee_bps is not None else args.taker_fee * 10_000.0,
    )
    params = {
        "strategy": strategy,
        "scenario": args.scenario,
        "gamma": args.gamma,
        "kappa": args.kappa,
        "horizon": args.horizon,
        "qty": args.qty,
        "maker_fee_bps": args.maker_fee_bps,
        "taker_fee_bps": args.taker_fee_bps,
    }
    return {
        "date": date,
        "strategy": strategy,
        "net_pnl": summary.get("net_pnl", 0.0),
        "gross_pnl": summary.get("gross_pnl", 0.0),
        "fees_paid": summary.get("fees_paid", 0.0),
        "maker_fees": summary.get("maker_fees", 0.0),
        "taker_fees": summary.get("taker_fees", 0.0),
        "fees": summary.get("fees", summary.get("fees_paid", 0.0)),
        "traded_notional": summary.get("traded_notional", 0.0),
        "breakeven_fee_bps": summary.get("breakeven_fee_bps", 0.0),
        "fee_sensitivity": summary.get("fee_sensitivity", {}),
        "reconciliation_residual": summary.get("reconciliation_residual", 0.0),
        "fills": summary.get("maker_fill_count", summary.get("fills", 0)),
        "max_drawdown": summary.get("max_drawdown", 0.0),
        "sharpe_ratio": summary.get("sharpe_ratio", summary.get("sharpe", 0.0)),
        "sortino_ratio": summary.get("sortino_ratio", summary.get("sortino", 0.0)),
        "mean_1s_markout": summary.get("mean_1s_markout"),
        "adverse_fill_rate": summary.get("adverse_fill_rate", 0.0),
        "pnl_reconciled": summary.get("pnl_reconciled", False),
        "traded_notional": summary.get("traded_notional", pnl.get("traded_notional", 0.0)),
        "breakeven_fee_bps": summary.get("breakeven_fee_bps", pnl.get("breakeven_fee_bps", 0.0)),
        "data_source": result.data_source,
        "mode": result.config.get("execution", {}).get("queue_model"),
        "parameter_hash": parameter_hash(params),
        **git_provenance(),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("--data-dir", type=Path, default=Path("data/raw"))
    parser.add_argument("--output-dir", type=Path, default=Path("results/research_suite"))
    parser.add_argument("--dates", type=str, default=None, help="Comma-separated UTC dates; default discovers matching captures")
    parser.add_argument("--scenario", choices=["optimistic", "baseline", "conservative"], default="conservative")
    parser.add_argument("--calibration-artifact", type=Path, default=None)
    parser.add_argument("--gamma", type=float, default=0.1)
    parser.add_argument("--kappa", type=float, default=1.5)
    parser.add_argument("--horizon", type=float, default=300.0)
    parser.add_argument("--qty", type=float, default=0.001)
    parser.add_argument("--half-spread", type=float, default=0.50)
    parser.add_argument("--min-spread", type=float, default=0.02)
    parser.add_argument(
        "--maker-fee", "--maker-fee-rate", dest="maker_fee", type=float, default=0.0002,
        help="Assumed maker fee rate; legacy rate form",
    )
    parser.add_argument(
        "--taker-fee", "--taker-fee-rate", dest="taker_fee", type=float, default=0.0005,
        help="Assumed forced-flatten fee rate; legacy rate form",
    )
    parser.add_argument(
        "--maker-fee-bps", type=float, default=None,
        help="Assumed maker fee in basis points; overrides the rate form",
    )
    parser.add_argument(
        "--taker-fee-bps", type=float, default=None,
        help="Assumed forced-flatten fee in basis points; overrides the rate form",
    )
    parser.add_argument("--quote-interval-ms", type=int, default=100)
    parser.add_argument("--markout-ms", type=int, default=1_000)
    parser.add_argument("--max-position", type=float, default=0.005)
    parser.add_argument("--max-drawdown", type=float, default=25.0)
    parser.add_argument("--bootstrap-replications", type=int, default=2_000)
    parser.add_argument("--confidence", type=float, default=0.95)
    parser.add_argument("--seed", type=int, default=42)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    calibration = (
        load_calibration_artifact(args.calibration_artifact, require_usable=True)
        if args.calibration_artifact is not None
        else None
    )
    if args.dates:
        dates = [item.strip() for item in args.dates.split(",") if item.strip()]
    else:
        dates = sorted(
            path.name.removeprefix("depth_").removesuffix(".parquet")
            for path in args.data_dir.glob("depth_*.parquet")
            if (args.data_dir / path.name.replace("depth_", "trades_")).is_file()
        )
    if not dates:
        raise SystemExit("no matching depth/trades captures found")

    replay_config = _config(args)
    factories = _factories(args, calibration)
    rows: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for date in dates:
        depth_path = args.data_dir / f"depth_{date}.parquet"
        trades_path = args.data_dir / f"trades_{date}.parquet"
        try:
            capture = HistoricalReplay.from_parquet(depth_path, trades_path, config=replay_config)
            for name, factory in factories.items():
                result = capture.run(factory)
                rows.append(_summary_row(date, name, result, args))
                print(f"{date} {name:>8} net={result.summary['net_pnl']: .6f}")
        except Exception as exc:
            failures.append({"date": date, "error": f"{type(exc).__name__}: {exc}"})
            print(f"{date} FAILED: {type(exc).__name__}: {exc}")

    if not rows:
        raise SystemExit("all requested captures failed; no comparison report written")
    frame = pd.DataFrame(rows)
    paired_differences: dict[str, float] = {}
    pivot = frame.pivot(index="date", columns="strategy", values="net_pnl")
    if {"as", "fixed"}.issubset(pivot.columns):
        paired_differences = {
            str(date): float(value)
            for date, value in (pivot["as"] - pivot["fixed"]).dropna().items()
        }
    summaries: dict[str, dict[str, Any]] = {}
    for strategy, group in frame.groupby("strategy", sort=True):
        enough_sessions = len(group) >= 6
        summaries[strategy] = {
            "sessions": int(len(group)),
            "net_pnl": bootstrap_mean_ci(
                group["net_pnl"].tolist(),
                replications=args.bootstrap_replications,
                confidence=args.confidence,
                seed=args.seed,
            ),
            "sharpe_ratio": bootstrap_mean_ci(
                group["sharpe_ratio"].tolist(),
                replications=args.bootstrap_replications,
                confidence=args.confidence,
                seed=args.seed + 1,
            ),
            "sortino_ratio": bootstrap_mean_ci(
                group["sortino_ratio"].tolist(),
                replications=args.bootstrap_replications,
                confidence=args.confidence,
                seed=args.seed + 2,
            ),
            "max_drawdown": bootstrap_mean_ci(
                group["max_drawdown"].tolist(),
                replications=args.bootstrap_replications,
                confidence=args.confidence,
                seed=args.seed + 3,
            ),
            "mean_fills": float(group["fills"].mean()),
            "mean_adverse_fill_rate": float(group["adverse_fill_rate"].mean()),
            "all_pnl_reconciled": bool(group["pnl_reconciled"].all()),
            "ci_status": "reported" if enough_sessions else "INSUFFICIENT SESSIONS: CI not reported",
        }
        if not enough_sessions:
            for key in ("net_pnl", "sharpe_ratio", "sortino_ratio", "max_drawdown"):
                summaries[strategy][key] = {
                    "observations": int(len(group)),
                    "estimate": float(group[key if key != "net_pnl" else "net_pnl"].mean()),
                    "lower": None,
                    "upper": None,
                    "confidence": args.confidence,
                    "replications": 0,
                }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    frame.to_csv(args.output_dir / "session_results.csv", index=False)
    report = {
        "runner": "scripts/run_research_suite.py",
        "config": {key: str(value) if isinstance(value, Path) else value for key, value in vars(args).items()},
        "dates": dates,
        "failures": failures,
        "strategies": summaries,
        "calibration": calibration.to_dict() if calibration is not None else None,
        "provenance": git_provenance(),
        "paired_as_minus_fixed": paired_differences,
        "paired_positive_count": sum(value > 0 for value in paired_differences.values()),
        "paired_non_positive_count": sum(value <= 0 for value in paired_differences.values()),
    }
    (args.output_dir / "comparison.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    markdown = [
        "# LOB-X Multi-session Research Comparison",
        "",
        f"Scenario: **{args.scenario}**; confidence: **{args.confidence:.0%}**",
        "",
        "| Strategy | Sessions | Mean net P&L | CI lower | CI upper | Mean Sharpe | Mean Sortino | Mean max DD |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for strategy, metrics in summaries.items():
        markdown.append(
            f"| **{strategy}** | {metrics['sessions']} | "
            f"{metrics['net_pnl']['estimate']:.6f} | {metrics['net_pnl']['lower'] if metrics['net_pnl']['lower'] is not None else 'NOT REPORTED'} | "
            f"{metrics['net_pnl']['upper'] if metrics['net_pnl']['upper'] is not None else 'NOT REPORTED'} | {metrics['sharpe_ratio']['estimate']:.4f} | "
            f"{metrics['sortino_ratio']['estimate']:.4f} | {metrics['max_drawdown']['estimate']:.6f} |"
        )
        markdown.append(f"|  |  |  |  |  |  |  |  |")
    if any(value["ci_status"] != "reported" for value in summaries.values()):
        markdown.append("\n**INSUFFICIENT SESSIONS: CI not reported**")
    markdown += [
        "",
        "## Per-session results",
        "",
        "| Date | Strategy | Fills | Traded notional | Gross | Fees | Net | Break-even fee (bps) | Data source | Mode | Parameter hash | Commit |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | --- | --- |",
    ]
    for row in rows:
        markdown.append(
            f"| {row['date']} | {row['strategy']} | {row['fills']} | {row['traded_notional']:.6f} | "
            f"{row['gross_pnl']:.6f} | {row['fees_paid']:.6f} | {row['net_pnl']:.6f} | "
            f"{row['breakeven_fee_bps']:.4f} | {row['data_source']} | {row['mode']} | "
            f"`{row['parameter_hash'][:12]}` | `{row['git_commit']}` |"
        )
    grouped = frame.pivot(index="date", columns="strategy", values="net_pnl")
    if {"as", "fixed"}.issubset(grouped.columns):
        differences = (grouped["as"] - grouped["fixed"]).dropna()
        markdown += [
            "",
            "## Paired AS minus fixed",
            "",
            f"Sign count positive / non-positive: **{int((differences > 0).sum())} / {int((differences <= 0).sum())}**",
            "",
        ]
        for date, value in differences.items():
            markdown.append(f"- `{date}`: {value:.6f}")
    markdown += ["", f"Failed captures: **{len(failures)}**", ""]
    (args.output_dir / "comparison.md").write_text("\n".join(markdown), encoding="utf-8")
    print(f"Reports saved under: {args.output_dir.resolve()}")


if __name__ == "__main__":
    main()
