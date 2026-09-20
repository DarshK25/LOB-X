"""Proof tests for the PnL waterfall used to explain live losses."""
import pytest

from lobx.analytics.pnl_waterfall import summarize_pnl_waterfall


def test_waterfall_separates_execution_edge_inventory_and_fees():
    result = summarize_pnl_waterfall(
        fills=[
            {"execution_edge": 0.02},
            {"execution_edge": 0.03},
        ],
        markouts=[
            {"available": True, "markout": -0.04},
            {"available": True, "markout": 0.01},
        ],
        realized_pnl=-0.20,
        unrealized_pnl=-0.01,
        fees=0.005,
    )
    assert result["execution_edge"] == pytest.approx(0.05)
    assert result["adverse_selection_markout"] == pytest.approx(-0.04)
    assert result["gross_pnl"] == pytest.approx(-0.21)
    assert result["net_pnl"] == pytest.approx(-0.215)
