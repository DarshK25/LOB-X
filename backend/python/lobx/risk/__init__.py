"""Risk controls shared by historical replay and paper trading.

Strategy code decides *where* it wants to quote.  This package decides
whether those quotes are permitted.  Keeping that boundary explicit prevents
a modelling bug from disabling a trading limit.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from lobx.execution.unwind import (
    AlmgrenChrissUnwindController,
    UnwindConfig,
    UnwindPlan,
)
from lobx.market_data.trade_stream import TradeTick
from lobx.risk.hawkes import (
    HawkesParameters,
    HawkesRegime,
    HawkesRiskConfig,
    HawkesRiskController,
    HawkesRiskDecision,
    load_hawkes_parameters,
    write_hawkes_artifact,
)
from lobx.strategy.base import QuotePair


class RiskLimits:
    """Hard limits enforced before order submission."""

    def __init__(
        self,
        max_position: float = 500.0,
        max_order_qty: int  = 100,
        max_drawdown: float = 10_000.0,
    ) -> None:
        self.max_position  = max_position
        self.max_order_qty = max_order_qty
        self.max_drawdown  = max_drawdown

    def check_order(self, qty: int, current_position: float, is_buy: bool) -> bool:
        """Returns True if order passes all limits."""
        if qty > self.max_order_qty:
            return False
        projected = current_position + (qty if is_buy else -qty)
        if abs(projected) > self.max_position:
            return False
        return True


class DrawdownMonitor:
    """Tracks peak equity and current drawdown."""

    def __init__(self) -> None:
        self._peak   = 0.0
        self._equity = 0.0

    def update(self, equity: float) -> float:
        """Update equity. Returns current drawdown (positive number = loss)."""
        self._equity = equity
        self._peak   = max(self._peak, equity)
        return self._peak - self._equity

    @property
    def drawdown(self) -> float:
        return self._peak - self._equity

    @property
    def peak(self) -> float:
        return self._peak


@dataclass(frozen=True)
class MarketMakingRiskConfig:
    """Hard, currency-denominated limits for one strategy session.

    ``max_position`` and ``max_order_qty`` are in base-asset units (BTC for
    BTCUSDT). ``max_drawdown`` is quote currency (USDT).  These are guardrails,
    not optimisation parameters, and should be deliberately small in paper
    trading.
    """

    max_position: float = 0.005
    max_order_qty: float = 0.001
    max_drawdown: float = 25.0
    max_notional: float = 500.0
    max_open_orders: int = 2
    stale_data_timeout_ms: int = 5_000
    # Risk controls are opt-in.  Keeping this disabled by default preserves the
    # historical fixed/AS benchmark semantics; production/paper runs can opt in
    # explicitly once Hawkes parameters and thresholds have been reviewed.
    hawkes: HawkesRiskConfig | None = None
    # A reduce-only plan is generated only when this is supplied.  The planner
    # never submits an exchange order; it is an auditable broker-facing output.
    unwind: UnwindConfig | None = None

    def __post_init__(self) -> None:
        if (
            self.max_position <= 0
            or self.max_order_qty <= 0
            or self.max_drawdown <= 0
            or self.max_notional <= 0
            or self.max_open_orders <= 0
            or self.stale_data_timeout_ms < 0
        ):
            raise ValueError("all risk limits must be positive")


class MarketMakingRiskManager:
    """Apply hard controls and Hawkes quote gating to a quote pair.

    Replay and live runners use this same boundary.  ``unwind_controller`` is
    optional because the resulting plan is broker-facing data only; when
    supplied, risk breaches generate a reduce-only plan but never submit it.
    """

    def __init__(
        self,
        config: MarketMakingRiskConfig | None = None,
        *,
        hawkes_controller: HawkesRiskController | None = None,
        unwind_controller: AlmgrenChrissUnwindController | None = None,
    ) -> None:
        self.config = config or MarketMakingRiskConfig()
        self._drawdown = DrawdownMonitor()
        self._halted = False
        self._halt_reason: str | None = None
        self.events: list[dict] = []
        self.hawkes_controller = hawkes_controller
        if self.hawkes_controller is None and self.config.hawkes is not None:
            self.hawkes_controller = HawkesRiskController(config=self.config.hawkes)
        self.unwind_controller = unwind_controller
        if self.unwind_controller is None and self.config.unwind is not None:
            self.unwind_controller = AlmgrenChrissUnwindController(self.config.unwind)
        self._last_unwind_plan: UnwindPlan | None = None

    @property
    def halted(self) -> bool:
        return self._halted

    @property
    def halt_reason(self) -> str | None:
        return self._halt_reason

    @property
    def drawdown(self) -> float:
        return self._drawdown.drawdown

    @property
    def hawkes_decision(self) -> HawkesRiskDecision | None:
        if self.hawkes_controller is None:
            return None
        return self.hawkes_controller.decision()

    @property
    def unwind_plan(self) -> UnwindPlan | None:
        return self._last_unwind_plan

    def on_trade(self, trade: TradeTick) -> HawkesRiskDecision | None:
        """Feed one public trade into the Hawkes controller, if enabled."""
        if self.hawkes_controller is None:
            return None
        return self.hawkes_controller.on_trade(trade.trade_id, trade.event_time)

    def update_equity(self, equity: float) -> None:
        """Mark equity and permanently halt quoting after a drawdown breach."""
        if self._halted:
            return
        drawdown = self._drawdown.update(equity)
        if drawdown >= self.config.max_drawdown:
            self._halted = True
            self._halt_reason = "max_drawdown"

    def apply_quotes(
        self,
        quotes: QuotePair,
        position: float,
        *,
        timestamp: int | None = None,
        market_mid: float | None = None,
        open_orders: int = 0,
        last_data_timestamp: int | None = None,
    ) -> QuotePair:
        """Apply Hawkes treatment, then all existing hard quote limits."""
        def block(side: str, reason: str, qty: float, price: float | None) -> None:
            allowed = max(self.config.max_position - abs(position), 0.0)
            self.events.append({
                "event_type": "RISK_BLOCK",
                "timestamp": timestamp,
                "side": side,
                "qty": qty,
                "price": price,
                "current_inventory": position,
                "allowed": allowed,
                "limit": self.config.max_position,
                "reason": reason,
            })

        if self.unwind_controller is not None:
            self.unwind_controller.update_position(position, timestamp)

        hawkes_decision: HawkesRiskDecision | None = None
        if self.hawkes_controller is not None:
            hawkes_decision = self.hawkes_controller.decision(timestamp)
            if hawkes_decision.quotes_pulled:
                if quotes.bid is not None:
                    block(
                        "bid",
                        "HAWKES_CASCADE" if hawkes_decision.regime is HawkesRegime.CASCADE else "HAWKES_COOLDOWN",
                        quotes.bid.qty,
                        quotes.bid.price,
                    )
                if quotes.ask is not None:
                    block(
                        "ask",
                        "HAWKES_CASCADE" if hawkes_decision.regime is HawkesRegime.CASCADE else "HAWKES_COOLDOWN",
                        quotes.ask.qty,
                        quotes.ask.price,
                    )
                self._trigger_unwind(position, timestamp, "hawkes_cascade")
                return QuotePair(bid=None, ask=None)

            adjusted_quotes = self.hawkes_controller.adjust_quotes(
                quotes,
                market_mid=market_mid,
                timestamp_ms=timestamp,
                decision=hawkes_decision,
            )
            if hawkes_decision.regime is HawkesRegime.ELEVATED:
                self.events.append({
                    "event_type": "RISK_ADJUSTMENT",
                    "timestamp": timestamp,
                    "reason": "HAWKES_ELEVATED",
                    "regime": hawkes_decision.regime.value,
                    "intensity": hawkes_decision.intensity,
                    "ratio": hawkes_decision.ratio,
                    "spread_multiplier": hawkes_decision.spread_multiplier,
                    "bid_price_before": quotes.bid.price if quotes.bid else None,
                    "ask_price_before": quotes.ask.price if quotes.ask else None,
                    "bid_price_after": adjusted_quotes.bid.price if adjusted_quotes.bid else None,
                    "ask_price_after": adjusted_quotes.ask.price if adjusted_quotes.ask else None,
                })
            quotes = adjusted_quotes

        if abs(position) > self.config.max_position:
            self._trigger_unwind(position, timestamp, "position_breach")

        if self._halted:
            if quotes.bid is not None:
                block("bid", "MAX_DRAWDOWN", quotes.bid.qty, quotes.bid.price)
            if quotes.ask is not None:
                block("ask", "MAX_DRAWDOWN", quotes.ask.qty, quotes.ask.price)
            return QuotePair(bid=None, ask=None)

        if (
            last_data_timestamp is not None
            and timestamp is not None
            and timestamp - last_data_timestamp > self.config.stale_data_timeout_ms
        ):
            if quotes.bid is not None:
                block("bid", "STALE_DATA_TIMEOUT", quotes.bid.qty, quotes.bid.price)
            if quotes.ask is not None:
                block("ask", "STALE_DATA_TIMEOUT", quotes.ask.qty, quotes.ask.price)
            return QuotePair(bid=None, ask=None)

        bid = quotes.bid
        ask = quotes.ask
        if bid is not None and bid.qty > self.config.max_order_qty:
            block("bid", "MAX_ORDER", bid.qty, bid.price)
            bid = None
        elif bid is not None and position + bid.qty > self.config.max_position:
            block("bid", "MAX_POSITION", bid.qty, bid.price)
            bid = None
        elif bid is not None and bid.price * bid.qty > self.config.max_notional:
            block("bid", "MAX_NOTIONAL", bid.qty, bid.price)
            bid = None
        if ask is not None and ask.qty > self.config.max_order_qty:
            block("ask", "MAX_ORDER", ask.qty, ask.price)
            ask = None
        elif ask is not None and position - ask.qty < -self.config.max_position:
            block("ask", "MAX_POSITION", ask.qty, ask.price)
            ask = None
        elif ask is not None and ask.price * ask.qty > self.config.max_notional:
            block("ask", "MAX_NOTIONAL", ask.qty, ask.price)
            ask = None
        if open_orders >= self.config.max_open_orders:
            if bid is not None:
                block("bid", "MAX_OPEN_ORDERS", bid.qty, bid.price)
                bid = None
            if ask is not None:
                block("ask", "MAX_OPEN_ORDERS", ask.qty, ask.price)
                ask = None
        return QuotePair(bid=bid, ask=ask)

    def _trigger_unwind(self, position: float, timestamp: int | None, reason: str) -> None:
        if self.unwind_controller is None or abs(position) <= 1e-12:
            return
        plan = self.unwind_controller.on_risk_trigger(
            position,
            timestamp_ms=0 if timestamp is None else timestamp,
            reason=reason,
        )
        self._last_unwind_plan = plan
        self.events.append({
            "event_type": "UNWIND_PLAN",
            "timestamp": timestamp,
            "reason": reason,
            "position": position,
            "reduce_only": True,
            "slice_count": len(plan.slices),
            "orders": plan.to_broker_orders(),
        })


__all__ = [
    "DrawdownMonitor",
    "HawkesParameters",
    "HawkesRegime",
    "HawkesRiskConfig",
    "HawkesRiskController",
    "HawkesRiskDecision",
    "load_hawkes_parameters",
    "write_hawkes_artifact",
    "MarketMakingRiskConfig",
    "MarketMakingRiskManager",
    "RiskLimits",
]
