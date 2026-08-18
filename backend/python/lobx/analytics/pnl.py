"""
P&L analytics.

Tracks realised P&L from a stream of trades for a designated trader.
"""
from __future__ import annotations
import lobx_cpp


class PnLTracker:
    """
    Computes realised and unrealised P&L for a single trader.

    Realised P&L is computed using average cost basis.
    """

    def __init__(self, trader_id: int) -> None:
        self._tid        = trader_id
        self._position   = 0.0
        self._cash       = 0.0
        self._cost_basis = 0.0  # average entry price

    def on_trade(self, trade: lobx_cpp.Trade) -> None:
        """Update position and cash from a trade."""
        is_buy = trade.buy_id == self._tid
        if not is_buy and trade.sell_id != self._tid:
            return  # Not our trade

        qty = float(trade.qty)
        px  = float(trade.price)

        if is_buy:
            # Update average cost
            total = self._position + qty
            if total > 0:
                self._cost_basis = (self._cost_basis * self._position + px * qty) / total
            self._position += qty
            self._cash     -= px * qty
        else:
            realised         = (px - self._cost_basis) * qty
            self._cash      += px * qty
            self._position  -= qty

    def unrealised(self, current_mid: float) -> float:
        return (current_mid - self._cost_basis) * self._position

    def realised(self) -> float:
        return self._cash + self._cost_basis * self._position

    @property
    def position(self) -> float:
        return self._position
