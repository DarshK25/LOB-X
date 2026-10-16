"""
strategy/base.py — core data types and Strategy ABC.

MarketState is the ONLY source of market information a strategy may use.
Adding a new input to a strategy (e.g. order-flow imbalance in Phase 5)
must go through adding a field here — no shared globals, no hidden state.

This keeps strategies:
  - Unit-testable without a live connection (pass a hand-crafted MarketState).
  - Swappable in the Phase 4 backtest harness (both strategies see identical
    MarketState objects from the same replay — apples-to-apples comparison).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

@dataclass(frozen=True)
class MarketState:
    """Canonical, evidence-backed snapshot of the current market state.

    The real dataclass fields are the legacy names used by the strategy codebase,
    and compatibility aliases are exposed via properties for the hardening-phase
    names requested in the audit. This keeps the state contract single-sourced and
    avoids duplicate implementations.
    """

    timestamp: int
    best_bid: float
    best_ask: float
    mid_price: float
    spread: float
    best_bid_qty: float
    best_ask_qty: float
    volatility: float = 0.0
    inventory: float = 0.0
    imbalance: float = 0.0
    microprice: float | None = None
    microprice_deviation: float = 0.0
    log_return: float = 0.0
    trade_price: float | None = None
    trade_quantity: float | None = None
    trade_side: str | None = None

    @property
    def mid(self) -> float:
        return self.mid_price

    @property
    def bid_depth(self) -> float:
        return self.best_bid_qty

    @property
    def ask_depth(self) -> float:
        return self.best_ask_qty

    @property
    def return_(self) -> float:
        return self.log_return

    @property
    def return_value(self) -> float:
        return self.log_return

    @property
    def return__(self) -> float:
        return self.log_return

    @classmethod
    def from_book(
        cls,
        *,
        timestamp: int,
        best_bid: float,
        best_bid_qty: float,
        best_ask: float,
        best_ask_qty: float,
        volatility: float,
        inventory: float,
        log_return: float = 0.0,
        trade_price: float | None = None,
        trade_quantity: float | None = None,
        trade_side: str | None = None,
    ) -> "MarketState":
        """Build the canonical state from one valid two-sided book."""
        if best_bid >= best_ask:
            raise ValueError("best_bid must be strictly below best_ask")
        total_depth = best_bid_qty + best_ask_qty
        mid = (best_bid + best_ask) / 2.0
        imbalance = (
            (best_bid_qty - best_ask_qty) / total_depth
            if total_depth > 0.0 else 0.0
        )
        microprice = (
            (best_ask * best_bid_qty + best_bid * best_ask_qty) / total_depth
            if total_depth > 0.0 else mid
        )
        return cls(
            timestamp=timestamp,
            best_bid=best_bid,
            best_ask=best_ask,
            mid_price=mid,
            spread=best_ask - best_bid,
            best_bid_qty=best_bid_qty,
            best_ask_qty=best_ask_qty,
            volatility=volatility,
            inventory=inventory,
            imbalance=imbalance,
            microprice=microprice,
            microprice_deviation=microprice - mid,
            log_return=log_return,
            trade_price=trade_price,
            trade_quantity=trade_quantity,
            trade_side=trade_side,
        )


@dataclass(frozen=True)
class Quote:
    """A single resting limit order to place.

    Attributes
    ----------
    price : quote price in the asset's native units (e.g. USD for BTC/USDT)
    qty   : order size (fractional BTC for BTC/USDT)
    """
    price: float
    qty:   float


@dataclass(frozen=True)
class QuotePair:
    """The bid and ask quotes returned by a strategy for one tick.

    Either side may be None, meaning "do not quote this side this tick"
    (e.g. a strategy might suppress the ask when already very short).
    """
    bid: Optional[Quote]
    ask: Optional[Quote]


class Strategy(ABC):
    """Abstract base class for all market-making strategies.

    A strategy is a PURE function of MarketState → QuotePair.
    No network I/O, no file writes, no shared state with the runner.
    Side effects are the StrategyRunner's job.

    This constraint makes strategies:
      1. Unit-testable without a live WebSocket connection.
      2. Deterministic across the Phase 4 backtester replay.
      3. Swappable at runtime — runner holds a Strategy reference.
    """

    @abstractmethod
    def on_tick(self, state: MarketState) -> QuotePair:
        """Called on every synced depth event.

        Parameters
        ----------
        state : frozen snapshot of current market state

        Returns
        -------
        QuotePair — the bid/ask quotes to place this tick.
                    None on either side means "cancel and do not requote."
        """
        ...

    def name(self) -> str:
        """Human-readable strategy name for logging."""
        return self.__class__.__name__
