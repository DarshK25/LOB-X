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

import logging
from dataclasses import dataclass, field

from lobx.execution.inventory import InventoryTracker
from lobx.execution.portfolio import PortfolioEngine
from lobx.market_data.trade_stream import TradeTick
from lobx.strategy.base import MarketState, QuotePair

logger = logging.getLogger(__name__)

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
    max_quote_age_ms: int | None = None

    def __post_init__(self) -> None:
        if self.maker_fee_rate < 0:
            raise ValueError("maker_fee_rate cannot be negative")
        if self.latency_ms < 0:
            raise ValueError("latency_ms cannot be negative")
        if not 0 < self.fill_ratio <= 1:
            raise ValueError("fill_ratio must be in (0, 1]")
        if self.queue_model not in {"shadow", "queue_ahead"}:
            raise ValueError("queue_model must be 'shadow' or 'queue_ahead'")
        if self.max_quote_age_ms is not None and self.max_quote_age_ms < 0:
            raise ValueError("max_quote_age_ms cannot be negative")


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
    activation_logged: bool = False
    status: str = "WAITING"
    canceled_at: int | None = None


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

    portfolio: PortfolioEngine = field(default_factory=PortfolioEngine)
    fills:     list             = field(default_factory=list)
    events:    list             = field(default_factory=list)
    assumptions: ExecutionAssumptions = field(default_factory=ExecutionAssumptions)

    # Internal resting orders — None when not quoted.
    _bid: ShadowOrder | None = field(default=None, init=False, repr=False)
    _ask: ShadowOrder | None = field(default=None, init=False, repr=False)
    _next_event_id: int = field(default=1, init=False, repr=False)
    _next_order_id: int = field(default=1, init=False, repr=False)
    _market_context: dict = field(default_factory=dict, init=False, repr=False)

    @property
    def inventory(self) -> InventoryTracker:
        return self.portfolio.inventory

    @property
    def fees_paid(self) -> float:
        return self.portfolio.fees

    def emit_event(self, event: dict) -> None:
        """Append one audit event with a stable process-local event ID."""
        self.events.append({"event_id": self._next_event_id, **event})
        self._next_event_id += 1

    def update_market_context(
        self, *, timestamp: int, mid: float, best_bid: float, best_ask: float
    ) -> None:
        """Store the latest market snapshot for subsequent fill forensics."""
        self._expire_stale_orders(timestamp)
        self._market_context = {
            "market_timestamp": timestamp,
            "market_mid": mid,
            "market_bid": best_bid,
            "market_ask": best_ask,
        }

    def record_quote_decision(
        self,
        *,
        strategy: str,
        state: MarketState,
        quotes: QuotePair,
        details: dict[str, object] | None = None,
    ) -> None:
        """Record the market and model inputs that produced one quote pair."""
        self.emit_event({
            "event_type": "QUOTE_CALCULATED",
            "timestamp": state.timestamp,
            "strategy": strategy,
            "market_mid": state.mid_price,
            "market_bid": state.best_bid,
            "market_ask": state.best_ask,
            "spread": state.spread,
            "imbalance": state.imbalance,
            "microprice": state.microprice,
            "microprice_deviation": state.microprice_deviation,
            "volatility": state.volatility,
            "return": getattr(state, "return_", state.log_return),
            "inventory": state.inventory,
            "trade_price": state.trade_price,
            "trade_quantity": state.trade_quantity,
            "trade_side": state.trade_side,
            "bid_price": quotes.bid.price if quotes.bid else None,
            "bid_qty": quotes.bid.qty if quotes.bid else None,
            "ask_price": quotes.ask.price if quotes.ask else None,
            "ask_qty": quotes.ask.qty if quotes.ask else None,
            "details": details or {},
            "reason": "strategy_decision",
        })

    def update_quotes(
        self,
        quotes: QuotePair,
        timestamp: int,
        bid_queue_ahead: float = 0.0,
        ask_queue_ahead: float = 0.0,
    ) -> None:
        """Cancel-replace bid/ask quotes when price has changed."""
        self.inventory.observe(timestamp)

        def cancel(order: ShadowOrder | None, side: str) -> None:
            if order is not None:
                order.status = "CANCELED"
                order.canceled_at = timestamp
                self.emit_event({
                    "event_type": "ORDER_CANCELED",
                    "timestamp": timestamp,
                    "order_id": order.order_id,
                    "side": side,
                    "price": order.price,
                    "qty": order.qty,
                    "remaining_qty": order.remaining_qty,
                    "queue_ahead_qty": order.queue_ahead_qty,
                    "status": order.status,
                    "reason": "quote_replaced",
                })

        # ── Bid side ─────────────────────────────────────────────────────────
        if quotes.bid is None:
            cancel(self._bid, "bid")
            self._bid = None
        elif self._bid is None or (
            abs(self._bid.price - quotes.bid.price) > 1e-9
            or abs(self._bid.qty - quotes.bid.qty) > 1e-12
        ):
            cancel(self._bid, "bid")
            self._bid = ShadowOrder(
                order_id=self._next_order_id,
                side="bid",
                price=quotes.bid.price,
                qty=quotes.bid.qty,
                remaining_qty=quotes.bid.qty,
                placed_at=timestamp,
                active_at=timestamp + self.assumptions.latency_ms,
                queue_ahead_qty=max(bid_queue_ahead, 0.0),
            )
            self._next_order_id += 1
            self.emit_event({
                "event_type": "ORDER_CREATED",
                "timestamp": timestamp,
                "order_id": self._bid.order_id,
                "side": "bid",
                "price": self._bid.price,
                "qty": self._bid.qty,
                "remaining_qty": self._bid.remaining_qty,
                "active_at": self._bid.active_at,
                "queue_ahead_qty": self._bid.queue_ahead_qty,
                "reason": "quote_created",
            })
            self.emit_event({
                "event_type": "ORDER_WAITING",
                "timestamp": timestamp,
                "active_at": self._bid.active_at,
                "order_id": self._bid.order_id,
                "side": "bid",
                "reason": "waiting_for_latency",
            })

        # ── Ask side ─────────────────────────────────────────────────────────
        if quotes.ask is None:
            cancel(self._ask, "ask")
            self._ask = None
        elif self._ask is None or (
            abs(self._ask.price - quotes.ask.price) > 1e-9
            or abs(self._ask.qty - quotes.ask.qty) > 1e-12
        ):
            cancel(self._ask, "ask")
            self._ask = ShadowOrder(
                order_id=self._next_order_id,
                side="ask",
                price=quotes.ask.price,
                qty=quotes.ask.qty,
                remaining_qty=quotes.ask.qty,
                placed_at=timestamp,
                active_at=timestamp + self.assumptions.latency_ms,
                queue_ahead_qty=max(ask_queue_ahead, 0.0),
            )
            self._next_order_id += 1
            self.emit_event({
                "event_type": "ORDER_CREATED",
                "timestamp": timestamp,
                "order_id": self._ask.order_id,
                "side": "ask",
                "price": self._ask.price,
                "qty": self._ask.qty,
                "remaining_qty": self._ask.remaining_qty,
                "active_at": self._ask.active_at,
                "queue_ahead_qty": self._ask.queue_ahead_qty,
                "reason": "quote_created",
            })
            self.emit_event({
                "event_type": "ORDER_WAITING",
                "timestamp": timestamp,
                "active_at": self._ask.active_at,
                "order_id": self._ask.order_id,
                "side": "ask",
                "reason": "waiting_for_latency",
            })

    def _activate_pending_orders(self, timestamp: int) -> None:
        """Emit ORDER_ACTIVE once an order is observed in the processing cycle.

        This audit event is intentionally separate from the execution gate. The
        fill engine still requires ``timestamp >= order.active_at`` before a quote
        can be crossed, but the ledger records the moment the order begins being
        processed for the audit trail.
        """
        for order in (self._bid, self._ask):
            if order is None or order.activation_logged:
                continue
            if timestamp < order.placed_at:
                continue
            self.emit_event({
                "event_type": "ORDER_ACTIVE",
                "timestamp": timestamp,
                "order_id": order.order_id,
                "side": order.side,
                "price": order.price,
                "qty": order.qty,
                "remaining_qty": order.remaining_qty,
                "active_at": order.active_at,
                "reason": "latency_elapsed",
            })
            order.status = "ACTIVE"
            order.activation_logged = True

    def on_trade(self, trade: TradeTick) -> None:
        """Check if a real market trade fills any of our resting shadow quotes.

        is_buyer_maker=True  → the aggressor was a SELLER (sell market order)
                               → this can only hit our resting BID.
        is_buyer_maker=False → the aggressor was a BUYER (buy market order)
                               → this can only hit our resting ASK.
        """
        self.inventory.observe(trade.event_time)
        self._activate_pending_orders(trade.event_time)
        self._expire_stale_orders(trade.event_time)

        self.emit_event({
            "event_type": "TRADE_RECEIVED",
            "timestamp": trade.event_time,
            "trade_id": trade.trade_id,
            "price": trade.price,
            "qty": trade.qty,
            "is_buyer_maker": trade.is_buyer_maker,
        })

        if (
            self._bid is not None
            and trade.is_buyer_maker
            and trade.event_time >= self._bid.active_at
            and trade.price <= self._bid.price
        ):
            self._bid.status = "ACTIVE"
            # Sell aggressor: fills resting bids at or above trade price.
            self._fill(self._bid, trade)

        if (
            self._ask is not None
            and not trade.is_buyer_maker
            and trade.event_time >= self._ask.active_at
            and trade.price >= self._ask.price
        ):
            self._ask.status = "ACTIVE"
            # Buy aggressor: fills resting asks at or below trade price.
            self._fill(self._ask, trade)

    def _fill(self, order: ShadowOrder, trade: TradeTick) -> None:
        """Execute a (possibly partial) fill and update inventory."""
        if order.status == "CANCELED":
            self.emit_event({
                "event_type": "FILL_REJECTED",
                "timestamp": trade.event_time,
                "order_id": order.order_id,
                "trade_id": trade.trade_id,
                "reason": "order_not_active",
            })
            return
        available_qty = trade.qty * self.assumptions.fill_ratio
        queue_consumed = 0.0
        if self.assumptions.queue_model == "queue_ahead":
            if (order.side == "bid" and trade.price < order.price) or (
                order.side == "ask" and trade.price > order.price
            ):
                order.queue_ahead_qty = 0.0
            queue_consumed = min(order.queue_ahead_qty, available_qty)
            order.queue_ahead_qty -= queue_consumed
            available_qty -= queue_consumed
            self.emit_event({
                "event_type": "QUEUE_UPDATED",
                "timestamp": trade.event_time,
                "order_id": order.order_id,
                "side": order.side,
                "price": order.price,
                "queue_ahead_qty": order.queue_ahead_qty,
                "remaining_qty": order.remaining_qty,
                "trade_id": trade.trade_id,
                "reason": "aggressor_filled_at_price",
            })
        if available_qty <= 0:
            return

        fill_qty = min(order.remaining_qty, available_qty)
        order.remaining_qty -= fill_qty

        side = "buy" if order.side == "bid" else "sell"
        fee = order.price * fill_qty * self.assumptions.maker_fee_rate
        self.portfolio.on_fill(side, order.price, fill_qty, fee)
        pnl = self.portfolio.snapshot(order.price)
        market_mid = self._market_context.get("market_mid")
        execution_edge = (
            (float(market_mid) - order.price) * fill_qty
            if side == "buy" and market_mid is not None
            else (order.price - float(market_mid)) * fill_qty
            if side == "sell" and market_mid is not None
            else 0.0
        )
        fill_record = {
            "order_id":  order.order_id,
            "side":      side,
            "price":     order.price,
            "qty":       fill_qty,
            "trade_id":  trade.trade_id,
            "timestamp": trade.event_time,
            "fee":       fee,
            "execution_edge": execution_edge,
            "gross_pnl": pnl["gross_pnl"],
            "net_pnl": pnl["net_pnl"],
            "order_created_at": order.placed_at,
            "order_age_ms": max(trade.event_time - order.placed_at, 0),
            "order_status": "ACTIVE",
            "quote_at_creation": order.price,
            **self._market_context,
        }
        self.fills.append(fill_record)
        self.emit_event({
            "event_type": "FILL",
            "timestamp": trade.event_time,
            "order_id": order.order_id,
            "trade_id": trade.trade_id,
            "side": side,
            "price": order.price,
            "qty": fill_qty,
            "remaining_qty": order.remaining_qty,
            "fee": fee,
            "gross_pnl": pnl["gross_pnl"],
            "net_pnl": pnl["net_pnl"],
            "execution_edge": execution_edge,
            "queue_ahead_qty": order.queue_ahead_qty,
            "inventory": self.inventory.position,
            "realized_pnl": self.inventory.realized_pnl,
            "reason": "aggressor_crossed_quote",
            "order_created_at": order.placed_at,
            "order_age_ms": max(trade.event_time - order.placed_at, 0),
            "order_status": "ACTIVE",
            "quote_at_creation": order.price,
            **self._market_context,
            "audit": {
                "public_trade_price": trade.price,
                "public_trade_qty": trade.qty,
                "aggressor_side": "sell" if trade.is_buyer_maker else "buy",
                "queue_consumed": queue_consumed,
                "crossed_through_quote": True,
            },
        })

        if fill_qty < order.qty:
            self.emit_event({
                "event_type": "PARTIAL_FILL",
                "timestamp": trade.event_time,
                "order_id": order.order_id,
                "side": side,
                "price": order.price,
                "qty": fill_qty,
                "remaining_qty": order.remaining_qty,
                "reason": "partial_fill",
            })

        logger.info(
            "SHADOW FILL  %s  %.6f @ %.2f  |  position=%.6f  realized_pnl=%.4f",
            side.upper(), fill_qty, order.price,
            self.inventory.position, self.inventory.realized_pnl,
        )

        # If fully filled, clear the slot so the runner knows to re-quote.
        if order.remaining_qty < 1e-12:
            order.status = "FILLED"
            if order.side == "bid":
                self._bid = None
            else:
                self._ask = None

    def _expire_stale_orders(self, timestamp: int) -> None:
        """Cancel quotes older than the configured age before matching trades."""
        max_age = self.assumptions.max_quote_age_ms
        if max_age is None:
            return
        for order, side in ((self._bid, "bid"), (self._ask, "ask")):
            if order is None or timestamp - order.placed_at <= max_age:
                continue
            order.status = "EXPIRED"
            order.canceled_at = timestamp
            self.emit_event({
                "event_type": "ORDER_EXPIRED",
                "timestamp": timestamp,
                "order_id": order.order_id,
                "side": side,
                "price": order.price,
                "order_age_ms": timestamp - order.placed_at,
                "reason": "max_quote_age",
            })
            if side == "bid":
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
        return self.portfolio.net_pnl(mark_price)
