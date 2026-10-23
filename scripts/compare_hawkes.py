"""Compare AS replay with Hawkes quote control off versus on."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1] / "backend" / "python"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from lobx.backtest.replay import BacktestConfig, HistoricalReplay
from lobx.execution.shadow_engine import ExecutionAssumptions
from lobx.risk import HawkesParameters, HawkesRiskConfig, MarketMakingRiskConfig
from lobx.strategy.avellaneda import AvellanedaStoikovStrategy


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=Path("data/raw"))
    parser.add_argument("--dates", required=True, help="Comma-separated real capture dates")
    parser.add_argument("--hawkes-artifact", type=Path, required=True)
    parser.add_argument("--gamma", type=float, default=0.1)
    parser.add_argument("--kappa", type=float, default=1.5)
    parser.add_argument("--horizon", type=float, default=300.0)
    parser.add_argument("--qty", type=float, default=0.001)
    args = parser.parse_args()
    payload = json.loads(args.hawkes_artifact.read_text(encoding="utf-8"))
    params = HawkesParameters.from_mapping(payload["parameters"])
    thresholds = payload.get("thresholds", {})
    elevated_ratio = float(
        thresholds.get("elevated_ratio", thresholds.get("elevated_intensity", params.mu * 2.0) / params.mu)
    )
    cascade_ratio = float(
        thresholds.get("cascade_ratio", thresholds.get("cascade_intensity", params.mu * 5.0) / params.mu)
    )
    print("date | hawkes | pulled_pct | fills | markout_1s | markout_5s | gross | net")
    print("-" * 105)
    for date in [item.strip() for item in args.dates.split(",") if item.strip()]:
        depth_path = args.data_dir / f"depth_{date}.parquet"
        trades_path = args.data_dir / f"trades_{date}.parquet"
        for enabled in (False, True):
            hawkes = (
                HawkesRiskConfig(
                    parameters=params,
                    elevated_ratio=elevated_ratio,
                    cascade_ratio=cascade_ratio,
                )
                if enabled else None
            )
            config = BacktestConfig(
                name=f"hawkes_{enabled}",
                execution=ExecutionAssumptions(maker_fee_rate=0.0002, latency_ms=100),
                risk=MarketMakingRiskConfig(
                    max_position=0.005,
                    max_order_qty=args.qty,
                    max_drawdown=25.0,
                    hawkes=hawkes,
                ),
            )
            replay = HistoricalReplay.from_parquet(depth_path, trades_path, config=config)
            result = replay.run(lambda: AvellanedaStoikovStrategy(
                gamma=args.gamma,
                kappa=args.kappa,
                horizon_seconds=args.horizon,
                qty=args.qty,
            ))
            quotes = [e for e in result.events if e.get("event_type") == "QUOTE_CALCULATED"]
            pulled = sum(e.get("bid_price") is None and e.get("ask_price") is None for e in quotes)
            summary = result.summary
            print(
                f"{date} | {'on' if enabled else 'off'} | "
                f"{(pulled / len(quotes) * 100 if quotes else 0):.3f} | "
                f"{summary.get('maker_fill_count', 0)} | {summary.get('mean_1s_markout')} | "
                f"{summary.get('mean_5000ms_markout')} | {summary.get('gross_pnl', 0.0):.6f} | "
                f"{summary.get('net_pnl', 0.0):.6f}"
            )


if __name__ == "__main__":
    main()
