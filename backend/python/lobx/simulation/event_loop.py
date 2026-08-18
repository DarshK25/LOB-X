"""
BaseTrader abstract class and EventLoop.

All trader archetypes inherit from BaseTrader and implement `step()`.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

import lobx_cpp

if TYPE_CHECKING:
    pass


class BaseTrader(ABC):
    """Abstract base for every simulated market participant."""

    def __init__(self, book: lobx_cpp.OrderBook, trader_id_start: int) -> None:
        self.book     = book
        self._next_id = trader_id_start

    def _new_id(self) -> int:
        oid = self._next_id
        self._next_id += 1
        return oid

    @abstractmethod
    def step(self) -> list[lobx_cpp.Trade]:
        """Called once per simulation tick. Returns trades generated."""
        ...
