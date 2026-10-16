"""PnL attribution helpers for paper-trading research reports."""
from __future__ import annotations

from collections.abc import Iterable


def summarize_pnl_waterfall(
    fills: Iterable[dict],
    markouts: Iterable[dict],
    *,
    realized_pnl: float,
    unrealized_pnl: float,
    fees: float,
) -> dict[str, float | int]:
    """Return an explicit, auditable PnL decomposition.

    ``execution_edge`` is the immediate quote-vs-mid edge at fill time. It is
    not realized profit; inventory PnL remains separately reported so the
    distinction between spread capture and later adverse price movement is
    visible.
    """
    fills = list(fills)
    markouts = list(markouts)
    execution_edge = sum(float(fill.get("execution_edge", 0.0)) for fill in fills)
    available = [record for record in markouts if record.get("available")]
    adverse_markout = sum(
        float(record.get("markout", 0.0))
        for record in available
        if float(record.get("markout", 0.0)) < 0.0
    )
    return {
        "execution_edge": execution_edge,
        "realized_inventory_pnl": float(realized_pnl),
        "unrealized_inventory_pnl": float(unrealized_pnl),
        "adverse_selection_markout": adverse_markout,
        "fees": float(fees),
        "gross_pnl": float(realized_pnl + unrealized_pnl),
        "net_pnl": float(realized_pnl + unrealized_pnl - fees),
        "fills_attributed": len(fills),
        "markouts_attributed": len(available),
    }
