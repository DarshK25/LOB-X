"""Reproducible experiment configuration, results, and statistical summaries."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class ExperimentConfig:
    experiment_id: str
    dataset: str
    strategy: str = "AS"
    gamma: float = 0.1
    kappa: float = 1.5
    order_qty: float = 0.001
    max_inventory: float = 0.005
    latency_ms: int = 100
    maker_fee: float = 0.0002
    taker_fee: float = 0.0005
    seed: int = 42
    volatility_method: str = "EWMA"
    volatility_lambda: float = 0.98
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def fingerprint(self) -> str:
        payload = json.dumps(self.to_dict(), sort_keys=True).encode()
        return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class StatisticalSummary:
    count: int
    mean: float
    median: float
    std: float
    percentile_5: float
    percentile_95: float
    sharpe: float
    max_drawdown: float


def summarize_returns(returns: list[float] | np.ndarray, *, max_drawdown: float = 0.0) -> StatisticalSummary:
    values = np.asarray(returns, dtype=float)
    if values.size == 0:
        return StatisticalSummary(0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, max_drawdown)
    std = float(np.std(values, ddof=1)) if values.size > 1 else 0.0
    return StatisticalSummary(
        count=int(values.size),
        mean=float(np.mean(values)),
        median=float(np.median(values)),
        std=std,
        percentile_5=float(np.percentile(values, 5)),
        percentile_95=float(np.percentile(values, 95)),
        sharpe=float(np.mean(values) / std) if std > 0 else 0.0,
        max_drawdown=float(max_drawdown),
    )


def write_experiment_result(
    root: str | Path,
    config: ExperimentConfig,
    *,
    summary: dict[str, Any],
    events: list[dict],
    fills: list[dict],
    quotes: list[dict] | None = None,
    pnl: list[dict] | None = None,
    inventory: list[dict] | None = None,
    markouts: list[dict] | None = None,
    calibration: dict[str, Any] | None = None,
) -> Path:
    """Write the standard experiment artifact directory."""
    output = Path(root) / config.experiment_id
    output.mkdir(parents=True, exist_ok=True)
    # JSON is a valid YAML 1.2 document and keeps artifact writing dependency-free.
    (output / "config.yaml").write_text(json.dumps(config.to_dict(), indent=2), encoding="utf-8")
    (output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    for name, rows in {
        "events": events, "fills": fills, "quotes": quotes or [],
        "pnl": pnl or [], "inventory": inventory or [], "markouts": markouts or [],
    }.items():
        pd.DataFrame(rows).to_csv(output / f"{name}.csv", index=False)
    (output / "calibration.json").write_text(json.dumps(calibration or {}, indent=2), encoding="utf-8")
    return output
