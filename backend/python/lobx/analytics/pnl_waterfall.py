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
    markout_horizon_ms: int | None = None,
) -> dict[str, object]:
    """Return an explicit, auditable PnL decomposition.

    ``execution_edge`` is the immediate quote-vs-mid edge at fill time. It is
    not realized profit; inventory PnL remains separately reported so the
    distinction between spread capture and later adverse price movement is
    visible.
    """
    fills = list(fills)
    markouts = list(markouts)
    execution_edge = sum(float(fill.get("execution_edge", 0.0)) for fill in fills)
    available = [
        record
        for record in markouts
        if record.get("available")
        and (
            markout_horizon_ms is None
            or int(record.get("horizon_ms", -1)) == markout_horizon_ms
        )
    ]
    adverse_markout = sum(
        float(record.get("markout", 0.0))
        for record in available
        if float(record.get("markout", 0.0)) < 0.0
    )
    realized_pnl = float(realized_pnl)
    unrealized_pnl = float(unrealized_pnl)
    fees = float(fees)
    gross_pnl = realized_pnl + unrealized_pnl
    net_pnl = gross_pnl - fees
    # Execution edge and markout are diagnostics about fills. They are not
    # added to the accounting waterfall because doing so would double-count
    # inventory PnL. The reconciled components below are the canonical ledger
    # equation used by PortfolioEngine.
    components = {
        "realized_inventory_pnl": realized_pnl,
        "unrealized_inventory_pnl": unrealized_pnl,
        "fees": fees,
        "gross_pnl": gross_pnl,
        "net_pnl": net_pnl,
    }
    reconciled_net_pnl = (
        components["realized_inventory_pnl"]
        + components["unrealized_inventory_pnl"]
        - components["fees"]
    )
    return {
        "execution_edge": execution_edge,
        "realized_inventory_pnl": realized_pnl,
        "unrealized_inventory_pnl": unrealized_pnl,
        "adverse_selection_markout": adverse_markout,
        "fees": fees,
        "gross_pnl": gross_pnl,
        "net_pnl": net_pnl,
        "pnl_components": components,
        "pnl_reconciliation_error": net_pnl - reconciled_net_pnl,
        "pnl_reconciled": abs(net_pnl - reconciled_net_pnl) <= 1e-12,
        "markout_horizon_ms": markout_horizon_ms,
        "fills_attributed": len(fills),
        "markouts_attributed": len(available),
    }
