"""
execution/shadow_engine.py — paper-trading book for OWN quotes.

v1 Fill Model (naive — documented tradeoff, not a bug):
  Any real Binance trade whose price crosses our resting quote price fills
  us immediately for min(our_qty, trade_qty).  This ignores queue position
  at that price level.

Why is this acceptable for Phase 2?
  1. For BTC/USDT with 1-tick spreads and high liquidity, resting at the
     top of book is common for a market maker — the queue-position effect
     is smaller than in illiquid markets.
  2. The alternative (queue-aware model) requires tracking total qty ahead
     of us at each price level from the depth feed, which you already have.
     This is Phase 4 Stretch Goal #1 — the implementation lives entirely in
     this file's _fill() method, so it won't require touching the runner.
  3. Naive fill overstates our fill rate (optimistic bias) — it's the right
     conservative assumption for a *maximum-performance* reference point.

Cancel-replace logic:
  Shadow orders are only replaced when price changes — this models the
  real behavior of a market-maker who leaves a quote in place as long as
  the model hasn't changed its mind, and only cancels when it reprices.
"""
from __future__ import annotations

import itertools
import logging
from dataclasses import dataclass, field

from lobx.strategy.base import QuotePair
from lobx.execution.inventory import InventoryTracker
from lobx.market_data.trade_stream import TradeTick

logger = logging.getLogger(__name__)

# Global counter — unique IDs across all shadow orders in a process lifetime.
_id_counter = itertools.count(1)


@dataclass(frozen=True)
class ExecutionAssumptions:
    """Explicit assumptions used by the paper fill model.

    ``shadow`` is deliberately optimistic: an eligible aggressor trade can
    immediately fill our quote. ``queue_ahead`` first consumes the displayed
    quantity that was ahead of us when the quote was placed.  It is still an
    approximation because public L2 data cannot reveal true queue priority.
    """

    maker_fee_rate: float = 0.0
    latency_ms: int = 0
    fill_ratio: float = 1.0
    queue_model: str = "shadow"

    def __post_init__(self) -> None:
        if self.maker_fee_rate < 0:
            raise ValueError("maker_fee_rate cannot be negative")
        if self.latency_ms < 0:
            raise ValueError("latency_ms cannot be negative")
        if not 0 < self.fill_ratio <= 1:
            raise ValueError("fill_ratio must be in (0, 1]")
        if self.queue_model not in {"shadow", "queue_ahead"}:
            raise ValueError("queue_model must be 'shadow' or 'queue_ahead'")


@dataclass
class ShadowOrder:
    """One resting shadow limit order.

    Attributes
    ----------
    order_id      : unique id for this shadow order instance
    side          : "bid" or "ask"
    price         : quoted price
    qty           : original order size
    remaining_qty : how much is still unfilled
    placed_at     : timestamp when this quote was placed (ms)
    """
    order_id:      int
    side:          str
    price:         float
    qty:           float
    remaining_qty: float
    placed_at:     int
    active_at:     int
    queue_ahead_qty: float = 0.0


@dataclass
class ShadowQuoteEngine:
    """Paper-trading order book for the strategy's OWN quotes.

    Holds at most ONE bid order and ONE ask order at any time (single-level
    market making).  cancel-replace happens automatically in update_quotes()
    whenever the strategy changes its price.

    Attributes (read-only)
    ----------------------
    inventory : InventoryTracker — position and PnL
    fills     : list of fill records (dicts) for post-session analysis
    """

    inventory: InventoryTracker = field(default_factory=InventoryTracker)
    fills:     list             = field(default_factory=list)
    assumptions: ExecutionAssumptions = field(default_factory=ExecutionAssumptions)
    fees_paid: float = field(default=0.0, init=False)

    # Internal resting orders — None when not quoted.
    _bid: ShadowOrder | None = field(default=None, init=False, repr=False)
    _ask: ShadowOrder | None = field(default=None, init=False, repr=False)

    def update_quotes(
        self,
        quotes: QuotePair,
        timestamp: int,
        bid_queue_ahead: float = 0.0,
        ask_queue_ahead: float = 0.0,
    ) -> None:
        """Cancel-replace bid/ask quotes when price has changed.

        Called by StrategyRunner after every strategy.on_tick().

        Logic (per side):
          - If new quote is None → cancel any existing order on that side.
          - If new quote price == current resting price → leave it (no cancel).
          - If new quote price != current resting price → cancel and re-place.
        """
        # ── Bid side ─────────────────────────────────────────────────────────
        if quotes.bid is None:
            self._bid = None
        elif self._bid is None or (
            abs(self._bid.price - quotes.bid.price) > 1e-9
            or abs(self._bid.qty - quotes.bid.qty) > 1e-12
        ):
            self._bid = ShadowOrder(
                order_id=next(_id_counter),
                side="bid",
                price=quotes.bid.price,
                qty=quotes.bid.qty,
                remaining_qty=quotes.bid.qty,
                placed_at=timestamp,
                active_at=timestamp + self.assumptions.latency_ms,
                queue_ahead_qty=max(bid_queue_ahead, 0.0),
            )

        # ── Ask side ─────────────────────────────────────────────────────────
        if quotes.ask is None:
            self._ask = None
        elif self._ask is None or (
            abs(self._ask.price - quotes.ask.price) > 1e-9
            or abs(self._ask.qty - quotes.ask.qty) > 1e-12
        ):
            self._ask = ShadowOrder(
                order_id=next(_id_counter),
                side="ask",
                price=quotes.ask.price,
                qty=quotes.ask.qty,
                remaining_qty=quotes.ask.qty,
                placed_at=timestamp,
                active_at=timestamp + self.assumptions.latency_ms,
                queue_ahead_qty=max(ask_queue_ahead, 0.0),
            )

    def on_trade(self, trade: TradeTick) -> None:
        """Check if a real market trade fills any of our resting shadow quotes.

        is_buyer_maker=True  → the aggressor was a SELLER (sell market order)
                               → this can only hit our resting BID.
        is_buyer_maker=False → the aggressor was a BUYER (buy market order)
                               → this can only hit our resting ASK.
        """
        if self._bid is not None and trade.is_buyer_maker and trade.event_time >= self._bid.active_at:
            # Sell aggressor: fills resting bids at or above trade price.
            if trade.price <= self._bid.price:
                self._fill(self._bid, trade)

        if self._ask is not None and not trade.is_buyer_maker and trade.event_time >= self._ask.active_at:
            # Buy aggressor: fills resting asks at or below trade price.
            if trade.price >= self._ask.price:
                self._fill(self._ask, trade)

    def _fill(self, order: ShadowOrder, trade: TradeTick) -> None:
        """Execute a (possibly partial) fill and update inventory."""
        available_qty = trade.qty * self.assumptions.fill_ratio
        if self.assumptions.queue_model == "queue_ahead":
            # A trade through our price necessarily removes displayed queue at
            # that level. For a trade exactly at our price, consume the queue
            # that was ahead before awarding a residual fill to our order.
            if (order.side == "bid" and trade.price < order.price) or (
                order.side == "ask" and trade.price > order.price
            ):
                order.queue_ahead_qty = 0.0
            consumed = min(order.queue_ahead_qty, available_qty)
            order.queue_ahead_qty -= consumed
            available_qty -= consumed
        if available_qty <= 0:
            return

        fill_qty = min(order.remaining_qty, available_qty)
        order.remaining_qty -= fill_qty

        side = "buy" if order.side == "bid" else "sell"
        self.inventory.on_fill(side, order.price, fill_qty)

        fee = order.price * fill_qty * self.assumptions.maker_fee_rate
        self.fees_paid += fee
        fill_record = {
            "order_id":  order.order_id,
            "side":      side,
            "price":     order.price,
            "qty":       fill_qty,
            "trade_id":  trade.trade_id,
            "timestamp": trade.event_time,
            "fee":       fee,
        }
        self.fills.append(fill_record)

        logger.info(
            "SHADOW FILL  %s  %.6f @ %.2f  |  position=%.6f  realized_pnl=%.4f",
            side.upper(), fill_qty, order.price,
            self.inventory.position, self.inventory.realized_pnl,
        )

        # If fully filled, clear the slot so the runner knows to re-quote.
        if order.remaining_qty < 1e-12:
            if order.side == "bid":
                self._bid = None
            else:
                self._ask = None

    # ── Convenience accessors ─────────────────────────────────────────────────

    @property
    def resting_bid(self) -> ShadowOrder | None:
        return self._bid

    @property
    def resting_ask(self) -> ShadowOrder | None:
        return self._ask

    @property
    def fill_count(self) -> int:
        return len(self.fills)

    def net_pnl(self, mark_price: float) -> float:
        """Mark-to-market P&L after explicitly modelled maker fees."""
        return self.inventory.total_pnl(mark_price) - self.fees_paid
