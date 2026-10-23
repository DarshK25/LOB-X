"""Run compatible A-S parameter sweeps with optional chronological OOS ranking.

The original ``run_parameter_sweep(parquet, gammas, kappas, max_trades,
output_dir)`` call remains valid.  New dimensions (inventory limit and spread
floor) are single-valued by default, while ``oos_selection=True`` evaluates
each candidate on a train prefix and untouched test suffix and records the
selected candidate by the requested objective.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

_BACKEND = Path(__file__).resolve().parents[1] / "backend" / "python"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from lobx.analytics.calibration import sha256_file
from lobx.analytics.walk_forward import chronological_split, hash_dataframe
from lobx.backtest.replay import BacktestConfig, HistoricalReplay
from lobx.execution.shadow_engine import ExecutionAssumptions
from lobx.risk import MarketMakingRiskConfig
from lobx.strategy.avellaneda import AvellanedaStoikovStrategy


def _as_float_grid(values: Iterable[float] | None, default: list[float]) -> list[float]:
    grid = default if values is None else [float(value) for value in values]
    if not grid or any(not math.isfinite(value) or value <= 0.0 for value in grid):
        raise ValueError("parameter grids must contain finite positive values")
    return grid


def _objective_value(summary: dict[str, Any], objective: str) -> float:
    """Map a public objective name to an OOS summary metric."""
    normalized = objective.lower().replace("-", "_")
    if normalized in {"net_pnl", "pnl"}:
        return float(summary.get("net_pnl", 0.0))
    if normalized in {"sharpe", "sharpe_ratio", "oos_sharpe"}:
        return float(summary.get("sharpe_ratio", summary.get("sharpe", 0.0)))
    if normalized in {"sortino", "sortino_ratio", "oos_sortino"}:
        return float(summary.get("sortino_ratio", summary.get("sortino", 0.0)))
    if normalized in {"risk_adjusted", "risk_adjusted_pnl", "calmar"}:
        drawdown = abs(float(summary.get("max_drawdown", 0.0)))
        return float(summary.get("net_pnl", 0.0)) / max(drawdown, 1e-12)
    raise ValueError(
        "objective must be one of net_pnl, sharpe, sortino, risk_adjusted, or calmar"
    )


def _make_config(
    *,
    inventory_limit: float,
    qty: float,
    quote_interval_ms: int,
    maker_fee_rate: float,
    taker_fee_rate: float,
) -> BacktestConfig:
    return BacktestConfig(
        name="sweep_run",
        quote_interval_ms=quote_interval_ms,
        flatten_at_end=True,
        taker_fee_rate=taker_fee_rate,
        execution=ExecutionAssumptions(maker_fee_rate=maker_fee_rate),
        risk=MarketMakingRiskConfig(
            max_position=inventory_limit,
            max_order_qty=qty,
        ),
    )


def _record(
    *,
    gamma: float,
    kappa: float,
    inventory_limit: float,
    spread_floor: float,
    horizon: float,
    summary: dict[str, Any],
    objective: str,
    sample: str,
) -> dict[str, Any]:
    turnover = float(summary.get("turnover", 0.0))
    net_pnl = float(summary.get("net_pnl", 0.0))
    result = {
        "gamma": gamma,
        "kappa": kappa,
        "inventory_limit": inventory_limit,
        "spread_floor": spread_floor,
        "horizon": horizon,
        "sample": sample,
        "net_pnl": net_pnl,
        "gross_pnl": summary.get("gross_pnl", 0.0),
        "realized_pnl": summary.get("realized_pnl", 0.0),
        "fees_paid": summary.get("fees_paid", 0.0),
        "maker_fees": summary.get("maker_fees", 0.0),
        "taker_fees": summary.get("taker_fees", 0.0),
        "fees": summary.get("fees", summary.get("fees_paid", 0.0)),
        "turnover": turnover,
        "traded_notional": summary.get("traded_notional", turnover),
        "breakeven_fee_bps": summary.get("breakeven_fee_bps", 0.0),
        "fee_sensitivity": summary.get("fee_sensitivity", {}),
        "pnl_per_turnover": net_pnl / turnover if turnover > 0.0 else 0.0,
        "fills": summary.get("fills", summary.get("maker_fill_count", 0)),
        "maker_fill_count": summary.get("maker_fill_count", 0),
        "max_abs_position": summary.get("max_abs_position", 0.0),
        "mean_abs_position": summary.get("mean_abs_position", 0.0),
        "inventory_variance": summary.get("inventory_variance", 0.0),
        "max_drawdown": summary.get("max_drawdown", 0.0),
        "mean_1s_markout": summary.get("mean_1s_markout", 0.0),
        "sharpe_ratio": summary.get("sharpe_ratio", summary.get("sharpe", 0.0)),
        "sortino_ratio": summary.get("sortino_ratio", summary.get("sortino", 0.0)),
        "pnl_return_period_ms": summary.get("pnl_return_period_ms"),
        "pnl_reconciled": summary.get("pnl_reconciled", False),
        "reconciliation_residual": summary.get("reconciliation_residual", 0.0),
    }
    result["objective"] = _objective_value(summary, objective)
    return result


def run_parameter_sweep(
    parquet_path: str | Path,
    gammas: list[float] | None = None,
    kappas: list[float] | None = None,
    max_trades: int | None = 50_000,
    output_dir: str | Path = "results/sweeps",
    inventory_limits: list[float] | None = None,
    spread_floors: list[float] | None = None,
    *,
    objective: str = "net_pnl",
    oos_selection: bool = False,
    train_fraction: float = 0.70,
    qty: float = 0.001,
    quote_interval_ms: int = 100,
    maker_fee_rate: float = 0.0,
    taker_fee_rate: float = 0.0005,
    maker_fee_bps: float | None = None,
    taker_fee_bps: float | None = None,
    horizon: float = 300.0,
) -> Path:
    if maker_fee_bps is not None:
        maker_fee_rate = float(maker_fee_bps) / 10_000.0
    if taker_fee_bps is not None:
        taker_fee_rate = float(taker_fee_bps) / 10_000.0
    if not math.isfinite(horizon) or horizon <= 0.0:
        raise ValueError("horizon must be finite and positive")
    gammas = _as_float_grid(gammas, [0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0])
    kappas = _as_float_grid(kappas, [0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 50.0])
    inventory_limits = _as_float_grid(inventory_limits, [0.005])
    # A floor of zero is useful for a pure model-spread experiment.
    spread_floors = [0.02] if spread_floors is None else [float(value) for value in spread_floors]
    if not spread_floors or any(not math.isfinite(value) or value < 0.0 for value in spread_floors):
        raise ValueError("spread_floors must contain finite non-negative values")
    _objective_value({}, objective)

    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    source = Path(parquet_path)
    if not source.exists():
        raise FileNotFoundError(f"Parquet dataset not found at: {source.resolve()}")
    raw = pd.read_parquet(source)

    if oos_selection:
        train, test = chronological_split(
            raw,
            train_fraction=train_fraction,
            max_trades=max_trades,
        )
        frames = {"train": train, "oos": test}
    else:
        frame = raw
        if max_trades is not None:
            if max_trades < 1:
                raise ValueError("max_trades must be positive")
            frame = frame.iloc[:max_trades].copy()
        frames = {"full": frame}

    replays = {
        (sample, inventory_limit): HistoricalReplay.from_binance_dataframe(
            frame,
            config=_make_config(
                inventory_limit=inventory_limit,
                qty=min(qty, inventory_limit),
                quote_interval_ms=quote_interval_ms,
                maker_fee_rate=maker_fee_rate,
                taker_fee_rate=taker_fee_rate,
            ),
        )
        for sample, frame in frames.items()
        for inventory_limit in inventory_limits
    }
    combinations = [
        (gamma, kappa, inventory_limit, spread_floor)
        for gamma in gammas
        for kappa in kappas
        for inventory_limit in inventory_limits
        for spread_floor in spread_floors
    ]
    print(f"Loading dataset: {source.name}...")
    print(f"Starting {len(combinations)} parameter combinations")

    records: list[dict[str, Any]] = []
    for index, (gamma, kappa, inventory_limit, spread_floor) in enumerate(combinations, start=1):
        for sample in frames:
            replay = replays[(sample, inventory_limit)]
            # Replays are independent and replay.run constructs a fresh
            # strategy, broker, inventory, and risk manager per invocation.
            result = replay.run(
                lambda gamma=gamma, kappa=kappa, inventory_limit=inventory_limit,
                spread_floor=spread_floor: AvellanedaStoikovStrategy(
                    gamma=gamma,
                    kappa=kappa,
                    qty=min(qty, inventory_limit),
                    horizon_seconds=horizon,
                    min_spread=spread_floor,
                )
            )
            records.append(
                _record(
                    gamma=gamma,
                    kappa=kappa,
                    inventory_limit=inventory_limit,
                    spread_floor=spread_floor,
                    horizon=horizon,
                    summary=result.summary,
                    objective=objective,
                    sample=sample,
                )
            )
        if index % 10 == 0 or index == len(combinations):
            print(f"  Completed {index}/{len(combinations)} combinations")

    frame_records = pd.DataFrame(records)
    frame_records.to_csv(out_path / "gamma_kappa_sweep.csv", index=False)

    # Preserve the original heatmap filenames. With multiple new dimensions,
    # pivot_table keeps every cell deterministic by taking the first exact
    # combination; the CSV retains the complete long-form result.
    selected_sample = "oos" if oos_selection else "full"
    selected = frame_records[frame_records["sample"] == selected_sample].copy()
    selected = selected.sort_values("objective", ascending=False, kind="stable")
    if not selected.empty:
        (out_path / "selected_parameters.json").write_text(
            json.dumps(selected.iloc[0].to_dict(), indent=2, default=str),
            encoding="utf-8",
        )

    matrix = selected.pivot_table(
        index="gamma", columns="kappa", values="net_pnl", aggfunc="first"
    )
    drawdown_matrix = selected.pivot_table(
        index="gamma", columns="kappa", values="max_drawdown", aggfunc="first"
    )
    fill_matrix = selected.pivot_table(
        index="gamma", columns="kappa", values="fills", aggfunc="first"
    )
    matrix.to_csv(out_path / "pnl_heatmap_matrix.csv")
    drawdown_matrix.to_csv(out_path / "drawdown_heatmap_matrix.csv")
    fill_matrix.to_csv(out_path / "fills_heatmap_matrix.csv")

    metadata = {
        "source": str(source),
        "source_sha256": sha256_file(source),
        "train_frame_sha256": hash_dataframe(frames["train"]) if oos_selection else None,
        "oos_frame_sha256": hash_dataframe(frames["oos"]) if oos_selection else None,
        "config": {
            "gammas": gammas,
            "kappas": kappas,
            "inventory_limits": inventory_limits,
            "spread_floors": spread_floors,
            "max_trades": max_trades,
            "objective": objective,
            "oos_selection": oos_selection,
            "train_fraction": train_fraction,
            "qty": qty,
            "quote_interval_ms": quote_interval_ms,
            "maker_fee_rate": maker_fee_rate,
            "taker_fee_rate": taker_fee_rate,
            "horizon": horizon,
            "maker_fee_bps": maker_fee_rate * 10_000.0,
            "taker_fee_bps": taker_fee_rate * 10_000.0,
        },
    }
    (out_path / "sweep_metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(f"Parameter sweep saved to: {out_path.resolve()}/")
    if not selected.empty:
        print(
            f"Selected {selected_sample} candidate by {objective}: "
            f"gamma={selected.iloc[0]['gamma']}, kappa={selected.iloc[0]['kappa']}, "
            f"inventory_limit={selected.iloc[0]['inventory_limit']}, "
            f"spread_floor={selected.iloc[0]['spread_floor']}"
        )
    return out_path


def _parse_grid(value: str | None) -> list[float] | None:
    return None if value is None else [float(item.strip()) for item in value.split(",") if item.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description="Run compatible Gamma x Kappa parameter sweep")
    parser.add_argument("--parquet", default="data/parquet/BTCUSDT-aggTrades-2024-08-01_to_2024-08-01.parquet")
    parser.add_argument("--max-trades", type=int, default=50000)
    parser.add_argument("--output-dir", default="results/sweeps")
    parser.add_argument("--gammas", type=str, default=None, help="Comma-separated gamma values")
    parser.add_argument("--kappas", type=str, default=None, help="Comma-separated kappa values")
    parser.add_argument("--inventory-limits", type=str, default=None, help="Comma-separated BTC limits")
    parser.add_argument("--spread-floors", type=str, default=None, help="Comma-separated minimum spreads")
    parser.add_argument(
        "--objective", "--oos-objective", dest="objective",
        choices=["net_pnl", "sharpe", "sortino", "risk_adjusted", "calmar"],
        default="net_pnl",
        help="Metric used to rank the selected full/OOS candidate",
    )
    parser.add_argument(
        "--oos-selection", action="store_true",
        help="Split chronologically and select the best candidate on untouched OOS results",
    )
    parser.add_argument("--train-fraction", type=float, default=0.70)
    parser.add_argument("--qty", type=float, default=0.001)
    parser.add_argument("--quote-interval-ms", type=int, default=100)
    parser.add_argument("--maker-fee-rate", "--maker-fee", dest="maker_fee_rate", type=float, default=0.0)
    parser.add_argument("--taker-fee-rate", "--taker-fee", dest="taker_fee_rate", type=float, default=0.0005)
    parser.add_argument("--maker-fee-bps", type=float, default=None)
    parser.add_argument("--taker-fee-bps", type=float, default=None)
    parser.add_argument("--horizon", type=float, default=300.0,
                        help="AS time horizon tau in seconds")
    args = parser.parse_args()
    run_parameter_sweep(
        parquet_path=args.parquet,
        gammas=_parse_grid(args.gammas),
        kappas=_parse_grid(args.kappas),
        max_trades=args.max_trades,
        output_dir=args.output_dir,
        inventory_limits=_parse_grid(args.inventory_limits),
        spread_floors=_parse_grid(args.spread_floors),
        objective=args.objective,
        oos_selection=args.oos_selection,
        train_fraction=args.train_fraction,
        qty=args.qty,
        quote_interval_ms=args.quote_interval_ms,
        maker_fee_rate=args.maker_fee_rate,
        taker_fee_rate=args.taker_fee_rate,
        maker_fee_bps=args.maker_fee_bps,
        taker_fee_bps=args.taker_fee_bps,
        horizon=args.horizon,
    )


if __name__ == "__main__":
    main()
