"""Event-driven paper exchange used by live simulation and historical backtests.

Each ``SimAccount`` owns one strategy, its orders and its cash/inventory. A
``MarketSimulator`` feeds every account the same market events, so a strategy
comparison is a single pass over the data and all accounts see identical
market conditions.

Realism modelled (all explicit in ``ExecutionConfig``):
  * feed latency + order latency: an order decided at t is live at
    t + feed + order; a cancel takes effect at the same delay and the old
    order can still fill while the cancel is in flight
  * post-only: an order that would cross the spread on arrival is rejected
  * tick size: quotes are on the price grid (strategies round, the exchange
    checks)
  * queue position: probabilistic model (see ``queue_model``) or an
    optimistic upper bound where we are always first in line
  * fills only from real trades at or through our price, partial fills,
    and fills when the opposite side of the book crosses our resting price
  * maker fee on passive fills (negative = rebate), taker fee on forced
    liquidation and on end-of-run flattening
  * rate limit: at most one new order per side per ``min_requote_ms``;
    a quote is only replaced when its price moves by ``requote_ticks``
  * hard position limit and drawdown kill switch (cancel + taker liquidation)

What is not modelled: our own orders do not change the public book, hidden
liquidity, exchange matching latency jitter, and self-impact of liquidations.
"""
from __future__ import annotations

from dataclasses import dataclass

from lobx.execution.queue_model import advance_on_depth_change
from lobx.strategy.base import MarketState, Strategy
from lobx.strategy.features import FeatureEngine

BID, ASK = 1, -1


@dataclass(frozen=True)
class ExecutionConfig:
    tick_size: float = 0.1
    maker_fee: float = 0.0002
    taker_fee: float = 0.0005
    feed_latency_ms: int = 5
    order_latency_ms: int = 10
    queue_model: str = "probabilistic"
    queue_power: float = 3.0
    min_requote_ms: int = 100
    requote_ticks: int = 1
    max_position: float = 0.01
    max_drawdown: float | None = None

    def __post_init__(self) -> None:
        if self.queue_model not in ("probabilistic", "optimistic"):
            raise ValueError("queue_model must be 'probabilistic' or 'optimistic'")
        if self.tick_size <= 0 or self.max_position <= 0:
            raise ValueError("tick_size and max_position must be positive")
        if min(self.feed_latency_ms, self.order_latency_ms, self.min_requote_ms) < 0:
            raise ValueError("latencies and rate limits cannot be negative")

    @property
    def round_trip_ms(self) -> int:
        return self.feed_latency_ms + self.order_latency_ms


class Order:
    __slots__ = ("oid", "side", "price", "qty", "remaining", "created_at", "arrive_at",
                 "cancel_at", "front", "prev_qty", "cum_trade")

    def __init__(self, oid: int, side: int, price: float, qty: float, created_at: int,
                 arrive_at: int) -> None:
        self.oid = oid
        self.side = side
        self.price = price
        self.qty = qty
        self.remaining = qty
        self.created_at = created_at
        self.arrive_at = arrive_at
        self.cancel_at: int | None = None
        self.front: float | None = None
        self.prev_qty: float | None = None
        self.cum_trade = 0.0


class SimAccount:
    """One strategy, its working orders and its PnL."""

    def __init__(self, strategy: Strategy, config: ExecutionConfig, name: str | None = None) -> None:
        self.strategy = strategy
        self.cfg = config
        self.name = name or strategy.name()
        self.position = 0.0
        self.cash = 0.0
        self.fees = 0.0
        self.avg_cost = 0.0
        self.realized = 0.0
        self.peak_equity = 0.0
        self.halted = False
        self.halt_reason: str | None = None
        self.fills: list[tuple] = []  # (ts, side, price, qty, fee, mid, maker)
        self.equity: list[float] = []
        self.positions: list[float] = []
        self.orders_sent = 0
        self.sent_qty = 0.0
        self.cancels_sent = 0
        self.rejects = 0
        self.quote_time_ms = 0
        self.live: list[Order] = []
        self.pending: list[Order] = []
        self._working: dict[int, Order | None] = {BID: None, ASK: None}
        self._last_send: dict[int, int] = {BID: -10**15, ASK: -10**15}
        self._next_oid = 1
        self._mid = 0.0
        # Optional event sink: listener(kind, fields). Used by the live session
        # recorder; None in backtests, so it costs one attribute check.
        self.listener = None

    def working_quote(self, side: int) -> "Order | None":
        """The order currently representing our quote on ``side`` (may be in flight)."""
        order = self._working[side]
        return order if order is not None and order.remaining > 1e-12 else None

    def _emit(self, kind: str, **fields) -> None:
        if self.listener is not None:
            self.listener(kind, {"acct": self.name, **fields})

    # ── accounting ──────────────────────────────────────────────────────────
    def _apply_fill(self, ts: int, side: int, price: float, qty: float, maker: bool,
                    oid: int = 0) -> None:
        fee = price * qty * (self.cfg.maker_fee if maker else self.cfg.taker_fee)
        signed = qty if side == BID else -qty
        pos = self.position
        if pos == 0 or (pos > 0) == (signed > 0):
            new_pos = pos + signed
            self.avg_cost = (self.avg_cost * abs(pos) + price * qty) / abs(new_pos)
        else:
            closing = min(qty, abs(pos))
            self.realized += (price - self.avg_cost) * closing * (1 if pos > 0 else -1)
            new_pos = pos + signed
            if abs(new_pos) < 1e-12:
                new_pos = 0.0
                self.avg_cost = 0.0
            elif (new_pos > 0) != (pos > 0):
                self.avg_cost = price
        self.position = new_pos
        self.cash -= signed * price
        self.fees += fee
        self.fills.append((ts, side, price, qty, fee, self._mid, maker))
        if self.listener is not None:
            self._emit("fill", ts=ts, oid=oid, side=side, px=price, qty=qty, fee=fee,
                       maker=maker, mid=self._mid, pos=self.position)

    def equity_at(self, mid: float) -> float:
        return self.cash + self.position * mid - self.fees

    # ── order lifecycle ─────────────────────────────────────────────────────
    def _cancel(self, order: Order, ts: int) -> None:
        if order.cancel_at is None:
            order.cancel_at = ts + self.cfg.round_trip_ms
            self.cancels_sent += 1
            if self.listener is not None:
                self._emit("cancel", ts=ts, oid=order.oid, effective=order.cancel_at)
        if self._working[order.side] is order:
            self._working[order.side] = None

    def cancel_all(self, ts: int) -> None:
        for order in self.live + self.pending:
            self._cancel(order, ts)

    def _send(self, side: int, price: float, qty: float, ts: int) -> None:
        order = Order(self._next_oid, side, price, qty, ts, ts + self.cfg.round_trip_ms)
        self._next_oid += 1
        self.pending.append(order)
        self._working[side] = order
        self._last_send[side] = ts
        self.orders_sent += 1
        self.sent_qty += qty
        if self.listener is not None:
            self._emit("order", ts=ts, oid=order.oid, side=side, px=price, qty=qty,
                       live_at=order.arrive_at)

    def _manage(self, side: int, quote, ts: int) -> None:
        cur = self._working[side]
        if cur is not None and cur.remaining <= 1e-12:
            cur = self._working[side] = None
        if quote is None:
            if cur is not None:
                self._cancel(cur, ts)
            return
        tick = self.cfg.tick_size
        if cur is not None and abs(cur.price - quote.price) < self.cfg.requote_ticks * tick - 1e-9 \
                and abs(cur.qty - quote.qty) < 1e-12:
            return
        if ts - self._last_send[side] < self.cfg.min_requote_ms:
            return
        if cur is not None:
            self._cancel(cur, ts)
        self._send(side, quote.price, quote.qty, ts)

    def decide(self, state: MarketState) -> None:
        ts = state.timestamp
        if self.halted:
            return
        quotes = self.strategy.on_tick(state, self.position)
        bid, ask = quotes.bid, quotes.ask
        limit = self.cfg.max_position + 1e-12
        if bid is not None and self.position + bid.qty > limit:
            bid = None
        if ask is not None and self.position - ask.qty < -limit:
            ask = None
        self._manage(BID, bid, ts)
        self._manage(ASK, ask, ts)

    # ── exchange side ───────────────────────────────────────────────────────
    def advance(self, ts: int, bid: float, bid_qty: float, ask: float, ask_qty: float) -> None:
        """Apply order arrivals and cancels that took effect at or before ts."""
        if self.pending:
            still = []
            for o in self.pending:
                if o.arrive_at > ts:
                    still.append(o)
                    continue
                if o.cancel_at is not None and o.cancel_at <= o.arrive_at:
                    continue
                crosses = o.price >= ask if o.side == BID else o.price <= bid
                if crosses or ask <= bid:
                    self.rejects += 1
                    if self._working[o.side] is o:
                        self._working[o.side] = None
                    if self.listener is not None:
                        self._emit("reject", ts=ts, oid=o.oid, reason="post_only")
                    continue
                best, best_qty = (bid, bid_qty) if o.side == BID else (ask, ask_qty)
                better = o.price > best if o.side == BID else o.price < best
                if better:
                    o.front = 0.0
                elif abs(o.price - best) < 1e-9:
                    o.front = best_qty
                    o.prev_qty = best_qty
                self.live.append(o)
            self.pending = still
        if self.live:
            self.live = [o for o in self.live
                         if o.remaining > 1e-12 and (o.cancel_at is None or o.cancel_at > ts)]

    def on_book(self, ts: int, bid: float, bid_qty: float, ask: float, ask_qty: float) -> None:
        self._mid = 0.5 * (bid + ask)
        if not self.live:
            return
        prob = self.cfg.queue_model == "probabilistic"
        n = self.cfg.queue_power
        for o in self.live:
            if o.side == BID:
                if ask <= o.price:
                    self._apply_fill(ts, BID, o.price, o.remaining, True, o.oid)
                    o.remaining = 0.0
                    continue
                best, best_qty, ahead = bid, bid_qty, o.price > bid
            else:
                if bid >= o.price:
                    self._apply_fill(ts, ASK, o.price, o.remaining, True, o.oid)
                    o.remaining = 0.0
                    continue
                best, best_qty, ahead = ask, ask_qty, o.price < ask
            if not prob:
                continue
            if abs(o.price - best) < 1e-9:
                if o.front is None:
                    o.front = best_qty
                elif o.prev_qty is None:
                    o.front = min(o.front, best_qty)
                else:
                    o.front = advance_on_depth_change(o.front, o.prev_qty, best_qty, o.cum_trade, n)
                o.prev_qty = best_qty
                o.cum_trade = 0.0
            elif ahead:
                o.front = 0.0
                o.prev_qty = None
                o.cum_trade = 0.0
            else:
                o.prev_qty = None
                o.cum_trade = 0.0
        self.live = [o for o in self.live if o.remaining > 1e-12]

    def on_trade(self, ts: int, price: float, qty: float, is_buyer_maker: bool) -> None:
        if not self.live:
            return
        side = BID if is_buyer_maker else ASK  # seller aggressor hits bids
        optimistic = self.cfg.queue_model == "optimistic"
        avail = qty
        for o in self.live:
            if o.side != side or avail <= 0:
                continue
            through = price < o.price if side == BID else price > o.price
            at = abs(price - o.price) < 1e-9
            if through or (at and optimistic):
                fill = min(o.remaining, avail)
            elif at:
                if o.front is None:
                    continue
                before = o.front
                o.front = max(0.0, before - avail)
                o.cum_trade += avail
                fill = min(o.remaining, max(0.0, avail - before))
            else:
                continue
            if fill > 0:
                self._apply_fill(ts, side, o.price, fill, True, o.oid)
                o.remaining -= fill
                avail -= fill
        self.live = [o for o in self.live if o.remaining > 1e-12]

    def mark(self, ts: int, bid: float, ask: float) -> None:
        """Equity sample; enforces the drawdown kill switch."""
        mid = 0.5 * (bid + ask)
        eq = self.equity_at(mid)
        self.equity.append(eq)
        self.positions.append(self.position)
        if eq > self.peak_equity:
            self.peak_equity = eq
        if (not self.halted and self.cfg.max_drawdown is not None
                and self.peak_equity - eq >= self.cfg.max_drawdown):
            self.halted = True
            self.halt_reason = "max_drawdown"
            self.cancel_all(ts)
            self.liquidate(ts, bid, ask)

    def liquidate(self, ts: int, bid: float, ask: float) -> None:
        """Close the position with a taker order at the touch."""
        if abs(self.position) < 1e-12:
            return
        if self.position > 0:
            self._apply_fill(ts, ASK, bid, self.position, False)
        else:
            self._apply_fill(ts, BID, ask, -self.position, False)


class MarketSimulator:
    """Drive several accounts from one market data stream."""

    def __init__(self, features: FeatureEngine, accounts: list[SimAccount],
                 decision_interval_ms: int = 100, sample_ms: int = 1000) -> None:
        self.features = features
        self.accounts = accounts
        self.decision_interval_ms = decision_interval_ms
        self.sample_ms = sample_ms
        self.sample_times: list[int] = []
        self._next_decision = 0
        self._next_sample: int | None = None
        self.book_events = 0
        self.trade_events = 0

    def on_book(self, ts: int, bid: float, bid_qty: float, ask: float, ask_qty: float) -> None:
        if not ask > bid > 0:
            return
        self.book_events += 1
        self.features.on_book(ts, bid, bid_qty, ask, ask_qty)
        for acc in self.accounts:
            acc.advance(ts, bid, bid_qty, ask, ask_qty)
            acc.on_book(ts, bid, bid_qty, ask, ask_qty)
        if self._next_sample is None:
            self._next_sample = ts - ts % self.sample_ms + self.sample_ms
        if ts >= self._next_sample:
            self.sample_times.append(ts)
            for acc in self.accounts:
                acc.mark(ts, bid, ask)
            self._next_sample = ts - ts % self.sample_ms + self.sample_ms
        if ts >= self._next_decision:
            state = self.features.state(ts)
            for acc in self.accounts:
                if acc.live or acc.pending:
                    acc.quote_time_ms += self.decision_interval_ms
                acc.decide(state)
            self._next_decision = ts + self.decision_interval_ms

    def on_trade(self, ts: int, price: float, qty: float, is_buyer_maker: bool) -> None:
        self.trade_events += 1
        self.features.on_trade(ts, price, qty, is_buyer_maker)
        f = self.features
        if not f.ready:
            return
        for acc in self.accounts:
            acc.advance(ts, f.bid, f.bid_qty, f.ask, f.ask_qty)
            acc.on_trade(ts, price, qty, is_buyer_maker)

    def finish(self, flatten: bool = True) -> None:
        f = self.features
        self.sample_times.append(f.last_ts)
        for acc in self.accounts:
            acc.cancel_all(f.last_ts)
            acc.live.clear()
            acc.pending.clear()
            if flatten:
                acc.liquidate(f.last_ts, f.bid, f.ask)
            acc.mark(f.last_ts, f.bid, f.ask)
