"""Risk controls shared by historical replay and paper trading.

Strategy code decides *where* it wants to quote.  This package decides
whether those quotes are permitted.  Keeping that boundary explicit prevents
a modelling bug from disabling a trading limit.
"""
from __future__ import annotations

from dataclasses import dataclass

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
    """Apply independent position and drawdown controls to a quote pair."""

    def __init__(self, config: MarketMakingRiskConfig | None = None) -> None:
        self.config = config or MarketMakingRiskConfig()
        self._drawdown = DrawdownMonitor()
        self._halted = False
        self._halt_reason: str | None = None
        self.events: list[dict] = []

    @property
    def halted(self) -> bool:
        return self._halted

    @property
    def halt_reason(self) -> str | None:
        return self._halt_reason

    @property
    def drawdown(self) -> float:
        return self._drawdown.drawdown

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
        """Remove quotes that would increase risk beyond a hard limit."""
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
