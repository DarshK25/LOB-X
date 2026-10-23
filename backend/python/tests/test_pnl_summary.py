"""Focused accounting tests for the fee-aware fill summary."""
import pytest

from lobx.analytics.pnl_summary import summarize_fills


def test_summarize_fills_exact_tuple_values():
    result = summarize_fills(
        fills=[
            ("buy", 100.0, 2.0, "maker"),
            ("sell", 110.0, 1.0, "taker"),
        ],
        final_mid=105.0,
        maker_bps=1.0,
        taker_bps=5.0,
    )

    assert result["gross_pnl"] == pytest.approx(15.0)
    assert result["maker_fees"] == pytest.approx(0.02)
    assert result["taker_fees"] == pytest.approx(0.055)
    assert result["fees"] == pytest.approx(0.075)
    assert result["net_pnl"] == pytest.approx(14.925)
    assert result["traded_notional"] == pytest.approx(310.0)
    assert result["breakeven_fee_bps"] == pytest.approx(15.0 / 310.0 * 10_000.0)
    assert result["fee_sensitivity_bps"][0] == pytest.approx(15.0)
    assert result["fee_sensitivity"][1] == pytest.approx(14.969)
    assert result["fee_sensitivity_bps"][1] == pytest.approx(14.969)
    assert result["fee_sensitivity_bps"][10] == pytest.approx(14.69)
    assert result["reconciliation_residual"] == pytest.approx(0.0)


def test_summarize_fills_supports_replay_dicts_and_forced_flatten_taker():
    result = summarize_fills(
        fills=[
            {"side": "buy", "price": 100.0, "qty": 1.0, "order_id": 7},
            {"side": "sell", "price": 105.0, "qty": 1.0, "order_id": "forced_flatten"},
        ],
        final_mid=102.0,
        maker_bps=2.0,
        taker_bps=10.0,
    )

    assert result["gross_pnl"] == pytest.approx(5.0)
    assert result["maker_fees"] == pytest.approx(0.02)
    assert result["taker_fees"] == pytest.approx(0.105)
    assert result["fees"] == pytest.approx(0.125)
    assert result["net_pnl"] == pytest.approx(4.875)
    assert result["pnl_reconciled"] is True
