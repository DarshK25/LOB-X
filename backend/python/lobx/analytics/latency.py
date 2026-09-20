"""Latency experiment helpers for replay result comparisons."""
from __future__ import annotations

from collections.abc import Callable, Iterable

import pandas as pd


def latency_sweep(
    latencies_ms: Iterable[int],
    evaluate: Callable[[int], dict[str, float]],
) -> pd.DataFrame:
    """Evaluate identical replay inputs under explicit latency assumptions."""
    rows = []
    for latency in latencies_ms:
        if latency < 0:
            raise ValueError("latency must be non-negative")
        row = dict(evaluate(int(latency)))
        row["latency_ms"] = int(latency)
        rows.append(row)
    return pd.DataFrame(rows).sort_values("latency_ms").reset_index(drop=True)
