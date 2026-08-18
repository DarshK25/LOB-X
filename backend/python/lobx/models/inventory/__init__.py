"""Inventory management models."""
from __future__ import annotations


class InventoryModel:
    """
    Simple inventory tracker with soft/hard limits.

    Tracks signed position and computes a skew signal
    to feed into the AS market maker.
    """

    def __init__(
        self,
        soft_limit: float = 100.0,
        hard_limit: float = 200.0,
    ) -> None:
        self._position  = 0.0
        self._soft      = soft_limit
        self._hard      = hard_limit

    @property
    def position(self) -> float:
        return self._position

    def fill(self, qty: float, is_buy: bool) -> None:
        """Register a fill."""
        self._position += qty if is_buy else -qty

    def skew(self) -> float:
        """
        Returns a normalised skew in [-1, +1].
        +1 = massively long → bias asks lower to offload
        -1 = massively short → bias bids higher to cover
        """
        return max(-1.0, min(1.0, self._position / self._soft))

    def is_at_hard_limit(self) -> bool:
        return abs(self._position) >= self._hard

    def reset(self) -> None:
        self._position = 0.0
