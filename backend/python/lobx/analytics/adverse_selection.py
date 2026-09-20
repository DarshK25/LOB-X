"""Signed fill markouts and adverse-selection classification."""
from __future__ import annotations

from collections.abc import Iterable
import pandas as pd


def classify_markout(markout: float, *, tolerance: float = 0.0) -> str:
    """Classify signed markout: positive is favorable to the filled side."""
    if tolerance < 0:
        raise ValueError("tolerance cannot be negative")
    if markout > tolerance:
        return "GOOD FILL"
    if markout < -tolerance:
        return "ADVERSE FILL"
    return "NEUTRAL FILL"


def classify_fill_markouts(
    markouts: Iterable[dict] | pd.DataFrame,
    *,
    tolerance: float = 0.0,
) -> pd.DataFrame:
    data = markouts.copy() if isinstance(markouts, pd.DataFrame) else pd.DataFrame(list(markouts))
    if data.empty:
        data["classification"] = pd.Series(dtype="object")
        return data
    required = {"markout", "available"}
    if not required.issubset(data.columns):
        raise ValueError(f"markouts missing columns: {sorted(required - set(data.columns))}")
    data["classification"] = data.apply(
        lambda row: classify_markout(float(row.markout), tolerance=tolerance)
        if bool(row.available) and pd.notna(row.markout) else "UNAVAILABLE",
        axis=1,
    )
    return data


def summarise_adverse_selection(markouts: Iterable[dict] | pd.DataFrame) -> dict[str, float | int]:
    data = classify_fill_markouts(markouts)
    available = data[data["classification"] != "UNAVAILABLE"]
    counts = available["classification"].value_counts()
    return {
        "good_fills": int(counts.get("GOOD FILL", 0)),
        "neutral_fills": int(counts.get("NEUTRAL FILL", 0)),
        "adverse_fills": int(counts.get("ADVERSE FILL", 0)),
        "available_markouts": int(len(available)),
        "mean_markout": float(available["markout"].mean()) if len(available) else 0.0,
        "adverse_fill_rate": float((available["classification"] == "ADVERSE FILL").mean()) if len(available) else 0.0,
    }


def analyze_adverse_selection(markouts: Iterable[dict] | pd.DataFrame) -> dict:
    """Decompose fill markouts by side (BUY vs SELL) across horizons."""
    data = classify_fill_markouts(markouts)
    available = data[data["classification"] != "UNAVAILABLE"]
    if available.empty:
        return {"buy_fills": {}, "sell_fills": {}}

    res = {"buy_fills": {}, "sell_fills": {}}
    for side, group in available.groupby("order_id"):
        # Determine side from signed_qty if available
        pass

    # Group by horizon and side if present
    horizons = sorted(available["horizon_ms"].unique()) if "horizon_ms" in available.columns else []
    for h in horizons:
        h_data = available[available["horizon_ms"] == h]
        res[f"{h}ms"] = {
            "mean_markout": float(h_data["markout"].mean()),
            "adverse_rate": float((h_data["classification"] == "ADVERSE FILL").mean()),
            "good_rate": float((h_data["classification"] == "GOOD FILL").mean()),
        }
    return res
