"""Almgren-Chriss-style reduce-only inventory unwind planning.

This is a planning boundary, not an exchange adapter.  It produces a
broker-facing sequence of reduce-only slices and never submits orders.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class UnwindConfig:
    """Parameters for a minimal Almgren--Chriss-style liquidation trajectory."""

    horizon_ms: int = 30_000
    interval_ms: int = 1_000
    risk_aversion: float = 1.0
    volatility: float = 0.1
    temporary_impact: float = 1.0

    def __post_init__(self) -> None:
        if self.horizon_ms <= 0 or self.interval_ms <= 0:
            raise ValueError("unwind horizon and interval must be positive")
        if self.risk_aversion < 0 or self.volatility < 0 or self.temporary_impact <= 0:
            raise ValueError("invalid unwind-planner parameters")


@dataclass(frozen=True)
class UnwindSlice:
    """One reduce-only broker-facing slice."""

    timestamp_ms: int
    side: str
    qty: float
    target_position: float
    reduce_only: bool = True

    def to_order(self) -> dict[str, object]:
        """Return an order-shaped payload for a broker adapter to inspect."""
        return {
            "timestamp_ms": self.timestamp_ms,
            "side": self.side,
            "qty": self.qty,
            "reduce_only": self.reduce_only,
            "target_position": self.target_position,
        }


@dataclass(frozen=True)
class UnwindPlan:
    """Complete trajectory generated for one inventory risk trigger."""

    generated_at_ms: int
    initial_position: float
    reason: str
    slices: tuple[UnwindSlice, ...]
    horizon_ms: int
    interval_ms: int
    risk_aversion: float
    volatility: float
    temporary_impact: float

    @property
    def target_positions(self) -> tuple[float, ...]:
        return tuple(slice_.target_position for slice_ in self.slices)

    @property
    def quantities(self) -> tuple[float, ...]:
        return tuple(slice_.qty for slice_ in self.slices)

    @property
    def final_position(self) -> float:
        return self.slices[-1].target_position if self.slices else self.initial_position

    def to_broker_orders(self) -> list[dict[str, object]]:
        """Expose plan data without performing I/O or order submission."""
        return [slice_.to_order() for slice_ in self.slices]

    def to_dict(self) -> dict[str, object]:
        return {
            "generated_at_ms": self.generated_at_ms,
            "initial_position": self.initial_position,
            "reason": self.reason,
            "reduce_only": True,
            "assumptions": {
                "horizon_ms": self.horizon_ms,
                "interval_ms": self.interval_ms,
                "risk_aversion": self.risk_aversion,
                "volatility": self.volatility,
                "temporary_impact": self.temporary_impact,
            },
            "orders": self.to_broker_orders(),
        }


class AlmgrenChrissUnwindController:
    """Generate and retain an Almgren-Chriss-style reduce-only plan.

    This controller plans slices only. It does not execute them or submit
    exchange orders; callers must provide a broker/execution adapter if they
    want to turn the plan into actual reduce-only trades.
    """

    def __init__(self, config: UnwindConfig | None = None) -> None:
        self.config = config or UnwindConfig()
        self.current_position = 0.0
        self.last_updated_at_ms: int | None = None
        self.last_trigger: str | None = None
        self.active_plan: UnwindPlan | None = None

    @property
    def state(self) -> dict[str, object]:
        """Small serializable state snapshot for risk telemetry."""
        return {
            "current_position": self.current_position,
            "last_updated_at_ms": self.last_updated_at_ms,
            "last_trigger": self.last_trigger,
            "has_active_plan": self.active_plan is not None,
        }

    def update_position(self, position: float, timestamp_ms: int | None = None) -> None:
        self.current_position = float(position)
        if timestamp_ms is not None:
            self.last_updated_at_ms = int(timestamp_ms)

    def generate_plan(
        self,
        position: float | None = None,
        *,
        timestamp_ms: int = 0,
        reason: str = "risk_trigger",
    ) -> UnwindPlan:
        """Generate a trajectory that never increases absolute inventory."""
        initial = self.current_position if position is None else float(position)
        if not math.isfinite(initial):
            raise ValueError("position must be finite")
        self.update_position(initial, timestamp_ms)

        slices: list[UnwindSlice] = []
        if abs(initial) > 1e-12:
            sign = 1.0 if initial > 0 else -1.0
            side = "sell" if sign > 0 else "buy"
            horizon_s = self.config.horizon_ms / 1_000.0
            kappa = math.sqrt(
                self.config.risk_aversion * self.config.volatility**2 / self.config.temporary_impact
            )
            denominator = math.sinh(kappa * horizon_s) if kappa > 0 else 0.0
            steps = max(1, math.ceil(self.config.horizon_ms / self.config.interval_ms))
            previous_target = initial
            for step in range(1, steps + 1):
                elapsed_ms = min(step * self.config.interval_ms, self.config.horizon_ms)
                remaining_s = (self.config.horizon_ms - elapsed_ms) / 1_000.0
                if kappa == 0.0:
                    fraction = max(remaining_s / horizon_s, 0.0)
                else:
                    fraction = math.sinh(kappa * remaining_s) / denominator
                target = sign * abs(initial) * max(min(fraction, 1.0), 0.0)
                quantity = abs(previous_target - target)
                if quantity > 1e-12:
                    slices.append(
                        UnwindSlice(
                            timestamp_ms=timestamp_ms + elapsed_ms,
                            side=side,
                            qty=quantity,
                            target_position=target,
                        )
                    )
                previous_target = target

        plan = UnwindPlan(
            generated_at_ms=int(timestamp_ms),
            initial_position=initial,
            reason=reason,
            slices=tuple(slices),
            horizon_ms=self.config.horizon_ms,
            interval_ms=self.config.interval_ms,
            risk_aversion=self.config.risk_aversion,
            volatility=self.config.volatility,
            temporary_impact=self.config.temporary_impact,
        )
        self.last_trigger = reason
        self.active_plan = plan
        return plan

    def on_risk_trigger(
        self,
        position: float,
        *,
        timestamp_ms: int = 0,
        reason: str = "risk_trigger",
    ) -> UnwindPlan:
        """Generate a fresh plan for a risk manager trigger."""
        return self.generate_plan(position, timestamp_ms=timestamp_ms, reason=reason)

    def clear_plan(self) -> None:
        self.active_plan = None


ReduceOnlyUnwindController = AlmgrenChrissUnwindController

__all__ = [
    "AlmgrenChrissUnwindController",
    "ReduceOnlyUnwindController",
    "UnwindConfig",
    "UnwindPlan",
    "UnwindSlice",
]
