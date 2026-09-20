"""Canonical portfolio accounting for paper and replay execution."""
from __future__ import annotations

from dataclasses import dataclass, field

from lobx.execution.inventory import InventoryTracker


@dataclass
class PortfolioEngine:
    """Track cash, inventory, realized/unrealized PnL, and fees.

    ``cash`` excludes fees so gross PnL remains the result of trading activity;
    fees are reported separately and subtracted by ``net_pnl``.
    """

    initial_capital: float = 0.0
    inventory: InventoryTracker = field(default_factory=InventoryTracker)
    fees: float = 0.0

    def __post_init__(self) -> None:
        if self.initial_capital < 0:
            raise ValueError("initial_capital cannot be negative")
        self.cash = self.initial_capital

    @property
    def position(self) -> float:
        return self.inventory.position

    @property
    def realized_pnl(self) -> float:
        return self.inventory.realized_pnl

    def on_fill(
        self,
        side: str,
        price: float,
        qty: float,
        fee: float = 0.0,
    ) -> dict[str, float]:
        """Apply one fill and return its accounting facts."""
        if fee < 0:
            raise ValueError("fee cannot be negative")
        signed_cash = -price * qty if side == "buy" else price * qty
        self.inventory.on_fill(side, price, qty)
        self.cash += signed_cash
        self.fees += fee
        return {
            "cash": self.cash,
            "realized_pnl": self.realized_pnl,
            "fee": fee,
        }

    def unrealized_pnl(self, mark_price: float) -> float:
        return self.inventory.unrealized_pnl(mark_price)

    def gross_pnl(self, mark_price: float) -> float:
        return self.cash + self.position * mark_price - self.initial_capital

    def net_pnl(self, mark_price: float) -> float:
        return self.gross_pnl(mark_price) - self.fees

    def snapshot(self, mark_price: float) -> dict[str, float]:
        """Return the complete canonical PnL breakdown at a mark price."""
        gross = self.gross_pnl(mark_price)
        return {
            "cash": self.cash,
            "inventory": self.position,
            "realized_pnl": self.realized_pnl,
            "unrealized_pnl": self.unrealized_pnl(mark_price),
            "fees": self.fees,
            "gross_pnl": gross,
            "net_pnl": gross - self.fees,
        }

    def reset(self) -> None:
        self.inventory.reset()
        self.cash = self.initial_capital
        self.fees = 0.0
