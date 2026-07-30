"""Risk management sub-package."""
from __future__ import annotations


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
