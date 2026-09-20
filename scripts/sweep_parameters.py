"""
sweep_parameters.py — Execute gamma x kappa parameter grid sweeps over historical datasets.

Runs an 8 x 7 = 56 parameter matrix sweep across risk-aversion gamma and order-flow
intensity kappa. Exports CSV summaries, metrics, and heatmap matrices to results/sweeps/.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

# Add backend directory to path
_BACKEND = Path(__file__).resolve().parents[1] / "backend" / "python"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from lobx.backtest.replay import BacktestConfig, HistoricalReplay
from lobx.strategy.avellaneda import AvellanedaStoikovStrategy


def run_parameter_sweep(
    parquet_path: str | Path,
    gammas: list[float] | None = None,
    kappas: list[float] | None = None,
    max_trades: int | None = 50_000,
    output_dir: str | Path = "results/sweeps",
) -> Path:
    if gammas is None:
        gammas = [0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0]
    if kappas is None:
        kappas = [0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 50.0]

    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    p_path = Path(parquet_path)
    if not p_path.exists():
        raise FileNotFoundError(f"Parquet dataset not found at: {p_path.resolve()}")

    print(f"Loading dataset: {p_path.name}...")
    df_raw = pd.read_parquet(p_path)
    if max_trades is not None and len(df_raw) > max_trades:
        df_raw = df_raw.iloc[:max_trades].copy()

    temp_sample_path = p_path.parent / f"_temp_sweep_{p_path.name}"
    df_raw.to_parquet(temp_sample_path, index=False)

    try:
        bt_config = BacktestConfig(
            name="sweep_run",
            quote_interval_ms=100,
            flatten_at_end=True,
            taker_fee_rate=0.0005,
        )
        replay = HistoricalReplay.from_binance_parquet(temp_sample_path, config=bt_config)

        sweep_records = []
        total_runs = len(gammas) * len(kappas)
        print(f"\nStarting {total_runs} parameter grid sweep runs (8 gammas x 7 kappas)...")
        print("=" * 78)

        run_idx = 0
        for g in gammas:
            for k in kappas:
                run_idx += 1
                res = replay.run(lambda g=g, k=k: AvellanedaStoikovStrategy(gamma=g, kappa=k, qty=0.001))
                s = res.summary

                turnover = s.get("turnover", 0.0)
                net_pnl = s.get("net_pnl", 0.0)
                pnl_per_turnover = (net_pnl / turnover) if turnover > 0 else 0.0

                record = {
                    "gamma": g,
                    "kappa": k,
                    "net_pnl": net_pnl,
                    "gross_pnl": s.get("gross_pnl", 0.0),
                    "realized_pnl": s.get("realized_pnl", 0.0),
                    "fees_paid": s.get("fees_paid", 0.0),
                    "turnover": turnover,
                    "pnl_per_turnover": pnl_per_turnover,
                    "fills": s.get("fills", 0),
                    "max_abs_position": s.get("max_abs_position", 0.0),
                    "mean_abs_position": s.get("mean_abs_position", 0.0),
                    "inventory_variance": s.get("inventory_variance", 0.0),
                    "max_drawdown": s.get("max_drawdown", 0.0),
                    "mean_1s_markout": s.get("mean_1s_markout", 0.0),
                }
                sweep_records.append(record)
                if run_idx % 10 == 0 or run_idx == total_runs:
                    print(f"  Completed {run_idx}/{total_runs} runs | gamma={g:<4} kappa={k:<4} -> Net PnL: ${net_pnl:>8.5f} Fills: {record['fills']}")

        df_sweep = pd.DataFrame(sweep_records)
        df_sweep.to_csv(out_path / "gamma_kappa_sweep.csv", index=False)

        # Pivot matrices for heatmaps
        pnl_matrix = df_sweep.pivot(index="gamma", columns="kappa", values="net_pnl")
        drawdown_matrix = df_sweep.pivot(index="gamma", columns="kappa", values="max_drawdown")
        fill_matrix = df_sweep.pivot(index="gamma", columns="kappa", values="fills")

        pnl_matrix.to_csv(out_path / "pnl_heatmap_matrix.csv")
        drawdown_matrix.to_csv(out_path / "drawdown_heatmap_matrix.csv")
        fill_matrix.to_csv(out_path / "fills_heatmap_matrix.csv")

        print("\n" + "=" * 78)
        print("                  NET PnL HEATMAP MATRIX (gamma x kappa)")
        print("=" * 78)
        print(pnl_matrix.to_string())
        print("=" * 78)
        print(f"\nParameter sweep matrices successfully saved to: {out_path.resolve()}/")
        return out_path

    finally:
        if temp_sample_path.exists():
            temp_sample_path.unlink()


def main():
    parser = argparse.ArgumentParser(description="Run Gamma x Kappa parameter sweep")
    parser.add_argument("--parquet", default="data/parquet/BTCUSDT-aggTrades-2024-08-01_to_2024-08-01.parquet")
    parser.add_argument("--max-trades", type=int, default=50000)
    parser.add_argument("--output-dir", default="results/sweeps")
    args = parser.parse_args()

    run_parameter_sweep(parquet_path=args.parquet, max_trades=args.max_trades, output_dir=args.output_dir)


if __name__ == "__main__":
    main()
