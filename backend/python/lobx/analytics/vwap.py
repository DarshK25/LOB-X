"""VWAP calculator."""
from __future__ import annotations
import lobx_cpp


class VWAPCalculator:
    """Rolling VWAP over a stream of trades."""

    def __init__(self) -> None:
        self._total_notional = 0.0
        self._total_qty      = 0

    def update(self, trade: lobx_cpp.Trade) -> None:
        self._total_notional += trade.price * trade.qty
        self._total_qty      += trade.qty

    @property
    def vwap(self) -> float | None:
        if self._total_qty == 0:
            return None
        return self._total_notional / self._total_qty

    def reset(self) -> None:
        self._total_notional = 0.0
        self._total_qty      = 0
