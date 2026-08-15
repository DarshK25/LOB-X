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
    """A frozen snapshot of all the market information a strategy may read.

    Attributes
    ----------
    timestamp       : exchange event time in milliseconds
    mid_price       : (best_bid + best_ask) / 2
    best_bid        : best resting bid price
    best_bid_qty    : quantity at best bid
    best_ask        : best resting ask price
    best_ask_qty    : quantity at best ask
    spread          : best_ask - best_bid
    volatility      : current EWMA volatility estimate (0.0 before warm-up)
    inventory       : current signed position (+ = long, - = short)
    """
    timestamp:    int
    mid_price:    float
    best_bid:     float
    best_bid_qty: float
    best_ask:     float
    best_ask_qty: float
    spread:       float
    volatility:   float
    inventory:    float


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
