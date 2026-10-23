"""Tests for reduce-only Almgren--Chriss-style unwind planning."""
from __future__ import annotations

import pytest

from lobx.execution.unwind import AlmgrenChrissUnwindController, UnwindConfig


def test_long_unwind_has_sell_slices_monotonically_approaching_zero():
    controller = AlmgrenChrissUnwindController(
        UnwindConfig(horizon_ms=4_000, interval_ms=1_000, risk_aversion=0.0)
    )
    plan = controller.generate_plan(0.4, timestamp_ms=10_000, reason="position_breach")
    assert plan.final_position == pytest.approx(0.0)
    assert all(item.side == "sell" for item in plan.slices)
    assert all(item.reduce_only for item in plan.slices)
    assert all(abs(a) >= abs(b) for a, b in zip(plan.target_positions, plan.target_positions[1:]))
    assert sum(plan.quantities) == pytest.approx(0.4)


def test_short_unwind_has_buy_slices_and_is_serializable():
    controller = AlmgrenChrissUnwindController(UnwindConfig(horizon_ms=2_000, interval_ms=1_000))
    plan = controller.on_risk_trigger(-0.2, timestamp_ms=5_000, reason="drawdown")
    assert plan.final_position == pytest.approx(0.0)
    assert all(item.side == "buy" for item in plan.slices)
    orders = plan.to_broker_orders()
    assert orders[-1]["reduce_only"] is True
    assert orders[-1]["target_position"] == pytest.approx(0.0)
