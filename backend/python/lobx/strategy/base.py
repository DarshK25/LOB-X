"""Core strategy types.

``MarketState`` is the only market information a strategy sees.  It does not
contain the account's position: one snapshot is shared by every strategy in a
comparison and each account passes its own position to ``on_tick``.

Units: prices USD, quantities in base asset (BTC), ``sigma`` in USD/sqrt(s),
``trade_rate`` in market orders per second.
"""
from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True, slots=True)
class MarketState:
    timestamp: int
    best_bid: float
    best_ask: float
    best_bid_qty: float
    best_ask_qty: float
    tick_size: float = 0.01
    sigma: float = 0.0
    hawkes_ratio: float = 1.0
    flow_imbalance: float = 0.0
    trade_rate: float = 0.0

    @property
    def mid(self) -> float:
        return 0.5 * (self.best_bid + self.best_ask)

    @property
    def spread(self) -> float:
        return self.best_ask - self.best_bid

    @property
    def imbalance(self) -> float:
        total = self.best_bid_qty + self.best_ask_qty
        return (self.best_bid_qty - self.best_ask_qty) / total if total > 0 else 0.0

    @property
    def microprice(self) -> float:
        total = self.best_bid_qty + self.best_ask_qty
        if total <= 0:
            return self.mid
        return (self.best_ask * self.best_bid_qty + self.best_bid * self.best_ask_qty) / total

    @property
    def microprice_deviation(self) -> float:
        return self.microprice - self.mid


@dataclass(frozen=True, slots=True)
class Quote:
    price: float
    qty: float


@dataclass(frozen=True, slots=True)
class QuotePair:
    """Either side may be None: do not quote that side."""
    bid: Optional[Quote]
    ask: Optional[Quote]


NO_QUOTES = QuotePair(None, None)


def round_bid(price: float, tick: float) -> float:
    return round(math.floor(price / tick + 1e-9) * tick, 10)


def round_ask(price: float, tick: float) -> float:
    return round(math.ceil(price / tick - 1e-9) * tick, 10)


def passive_pair(state: MarketState, bid: float | None, ask: float | None,
                 qty_bid: float, qty_ask: float) -> QuotePair:
    """Round to ticks and keep both quotes passive (post-only would reject a cross)."""
    tick = state.tick_size
    b = a = None
    if bid is not None and qty_bid > 0:
        b = min(round_bid(bid, tick), round(state.best_ask - tick, 10))
    if ask is not None and qty_ask > 0:
        a = max(round_ask(ask, tick), round(state.best_bid + tick, 10))
    if b is not None and a is not None and b >= a:
        a = round(b + tick, 10)
    return QuotePair(Quote(b, qty_bid) if b is not None else None,
                     Quote(a, qty_ask) if a is not None else None)


class Strategy(ABC):
    """A pure function of (MarketState, position) -> QuotePair."""

    qty: float = 0.001
    label: str | None = None

    @abstractmethod
    def on_tick(self, state: MarketState, position: float) -> QuotePair:
        ...

    def name(self) -> str:
        return self.label or self.__class__.__name__

    def lots(self, position: float) -> float:
        return position / self.qty if self.qty > 0 else 0.0
