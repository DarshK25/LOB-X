"""Observed session statistics and uncalibrated activity for one live market.

All public timestamps are exchange milliseconds; only the canonical Hawkes
model receives seconds. No history, quotes, or arrivals are manufactured here.
"""
from __future__ import annotations

from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from math import isfinite
from numbers import Integral, Real
from typing import Literal

from lobx.market_data.trade_stream import TradeTick
from lobx.models.hawkes.intensity import HawkesIntensity

HawkesRegime = Literal["QUIET", "ACTIVE", "BURST"]
BookSide = Sequence[tuple[float, float]]


@dataclass(frozen=True, slots=True)
class LiveSignalsSnapshot:
    """A detached, immutable session view.

    ``mid``, ``spread``, ``microprice`` and ``microprice_offset`` are quote-price
    units; offset is microprice minus mid. ``spread_bps`` is spread / mid * 10_000.
    Microprice weights the ask by best-bid quantity and the bid by best-ask
    quantity. ``imbalance`` is signed L1 OBI, (bid_qty - ask_qty) / total_L1_qty,
    in [-1, 1]. All six fields are None until a valid, unlocked, two-sided book
    arrives, and become None immediately on an invalid book update.

    ``vwap`` is session sum(price * qty) / total_volume, or None without trades.
    ``total_volume`` and ``buy_volume`` are observed base-asset quantities.
    ``buy_fraction`` is buy_volume / total_volume in [0, 1], or None at zero
    volume (multiply by 100 for a percentage). A buy has is_buyer_maker=False.
    ``trade_count`` counts accepted unique trades, including late ones.

    ``hawkes_intensity`` is an UNCALIBRATED events/second estimate using fixed
    mu=0.5, alpha=0.8, beta=2. ``hawkes_ratio`` divides it by the fixed heuristic
    background mu=0.5 events/s, NOT an estimated arrival rate or stationary
    mean. Regimes are heuristic ratio bands: QUIET < 2, ACTIVE [2, 5), BURST >= 5.
    They are activity labels, not calibrated cascade detection or trading advice.

    ``book_timestamp`` is the latest submitted book's exchange time in ms, even
    for invalid book contents (None if absent/invalid time). Books are applied
    in delivery order. ``last_trade_timestamp`` is the maximum accepted trade
    time in ms, or None. No automatic book-age cutoff is imposed: the caller
    uses these timestamps to decide staleness for its feed/UI.
    """

    mid: float | None
    spread: float | None
    spread_bps: float | None
    microprice: float | None
    microprice_offset: float | None
    imbalance: float | None
    vwap: float | None
    total_volume: float
    buy_volume: float
    buy_fraction: float | None
    trade_count: int
    hawkes_intensity: float
    hawkes_ratio: float
    hawkes_regime: HawkesRegime
    book_timestamp: float | None
    last_trade_timestamp: float | None


def _positive_number(value: object) -> float | None:
    """Accept finite positive real numbers, not booleans or numeric strings."""
    if isinstance(value, bool) or not isinstance(value, Real):
        return None
    try:
        number = float(value)
    except (OverflowError, ValueError):
        return None
    return number if isfinite(number) and number > 0.0 else None


def _best_level(side: BookSide, *, descending: bool) -> tuple[float, float] | None:
    """Validate all levels and best-first ordering, returning only L1."""
    best = None
    previous = None
    for level in side:
        try:
            raw_price, raw_qty = level
        except (TypeError, ValueError):
            return None
        price, qty = _positive_number(raw_price), _positive_number(raw_qty)
        if price is None or qty is None:
            return None
        if previous is not None and (
            (descending and price > previous) or (not descending and price < previous)
        ):
            return None
        if best is None:
            best = (price, qty)
        previous = price
    return best


class LiveMarketSignals:
    """Incremental, in-memory signals for a single market/session.

    Prices, quantities and timestamps must be finite positive real numbers.
    Invalid books clear book signals; invalid trades are ignored atomically.
    Trades must have nonnegative integer IDs, a boolean maker flag, a nonempty
    symbol, and representable positive notional/session totals. The first
    accepted trade binds the session's symbol; other symbols are ignored.

    Deduplication keeps only the last ``dedup_capacity`` accepted trade IDs
    (default 4096), FIFO. Duplicate deliveries do not refresh their age. An ID
    replayed after eviction is accepted again: this is bounded stream hygiene,
    not persistent exactly-once accounting. No raw trade history is retained.

    Unique late trades still contribute to volume, VWAP, side and count, but a
    timestamp strictly older than the last Hawkes arrival does NOT excite the
    model or rewind it. Equal-time distinct trades do excite it. This deliberate
    online approximation avoids turning old delayed trades into a current burst.
    Snapshots decay without arrivals, with evaluation time clamped to both the
    latest Hawkes event and previous evaluation time, so skew cannot reverse
    decay. Call from one serialized feed/UI loop; no thread safety is implied.
    """

    def __init__(self, *, dedup_capacity: int = 4096) -> None:
        if (
            isinstance(dedup_capacity, bool)
            or not isinstance(dedup_capacity, Integral)
            or dedup_capacity <= 0
        ):
            raise ValueError("dedup_capacity must be a positive integer")
        self._dedup_capacity = dedup_capacity
        self._seen_ids: set[int] = set()
        self._id_order: deque[int] = deque()
        self._symbol: str | None = None
        self._book: tuple[float, float, float, float, float, float] | None = None
        self._book_timestamp: float | None = None
        self._last_trade_timestamp: float | None = None
        self._total_volume: float = 0.0
        self._buy_volume: float = 0.0
        self._notional: float = 0.0
        self._trade_count: int = 0
        self._hawkes: HawkesIntensity = HawkesIntensity(mu=0.5, alpha=0.8, beta=2.0)
        self._last_hawkes_s: float = 0.0
        self._evaluation_s: float = 0.0

    def on_book(self, bids: BookSide, asks: BookSide, timestamp: float) -> None:
        """Replace the best-first book, invalidating old values before validation."""
        self._book = None
        self._book_timestamp = _positive_number(timestamp)
        if self._book_timestamp is None:
            return
        bid = _best_level(bids, descending=True)
        ask = _best_level(asks, descending=False)
        if bid is None or ask is None or bid[0] >= ask[0]:
            return

        spread = ask[0] - bid[0]
        mid = bid[0] + spread / 2.0
        # Scale quantities before summing to avoid overflowing otherwise valid L1.
        scale = max(bid[1], ask[1])
        bid_scaled, ask_scaled = bid[1] / scale, ask[1] / scale
        bid_weight = bid_scaled / (bid_scaled + ask_scaled)
        microprice = bid[0] + spread * bid_weight
        self._book = (
            mid, spread, spread / mid * 10_000.0,
            microprice, microprice - mid, 2.0 * bid_weight - 1.0,
        )

    def on_trade(self, tick: TradeTick) -> None:
        """Accumulate a valid nonduplicate trade; late trades skip Hawkes only."""
        price = _positive_number(tick.price)
        qty = _positive_number(tick.qty)
        timestamp = _positive_number(tick.event_time)
        if price is None or qty is None or timestamp is None:
            return
        if isinstance(tick.trade_id, bool) or not isinstance(tick.trade_id, Integral):
            return
        if tick.trade_id < 0 or not isinstance(tick.is_buyer_maker, bool):
            return
        if not isinstance(tick.symbol, str) or not tick.symbol.strip():
            return
        if self._symbol is not None and tick.symbol != self._symbol:
            return
        trade_id = int(tick.trade_id)
        if trade_id in self._seen_ids:
            return

        notional = price * qty
        total_volume = self._total_volume + qty
        total_notional = self._notional + notional
        buy_volume = self._buy_volume + (0.0 if tick.is_buyer_maker else qty)
        if not all(isfinite(value) and value > 0.0 for value in (
            notional, total_volume, total_notional,
        )):
            return

        self._symbol = tick.symbol
        self._total_volume = total_volume
        self._buy_volume = buy_volume
        self._notional = total_notional
        self._trade_count += 1
        self._last_trade_timestamp = max(self._last_trade_timestamp or timestamp, timestamp)
        self._seen_ids.add(trade_id)
        self._id_order.append(trade_id)
        if len(self._id_order) > self._dedup_capacity:
            self._seen_ids.remove(self._id_order.popleft())

        event_s = timestamp / 1_000.0
        if event_s >= self._last_hawkes_s:
            self._hawkes.update(event_s)
            self._last_hawkes_s = event_s

    def snapshot(self, now_ms: float) -> LiveSignalsSnapshot:
        """Evaluate without an arrival; invalid evaluation times raise ValueError."""
        now = _positive_number(now_ms)
        if now is None:
            raise ValueError("now_ms must be finite and positive (exchange milliseconds)")
        self._evaluation_s = max(self._evaluation_s, self._last_hawkes_s, now / 1_000.0)
        intensity = float(self._hawkes.evaluate(self._evaluation_s))
        ratio = intensity / 0.5
        regime: HawkesRegime = "BURST" if ratio >= 5.0 else "ACTIVE" if ratio >= 2.0 else "QUIET"
        mid, spread, spread_bps, microprice, offset, imbalance = self._book or (None,) * 6
        has_volume = self._total_volume > 0.0
        return LiveSignalsSnapshot(
            mid=mid,
            spread=spread,
            spread_bps=spread_bps,
            microprice=microprice,
            microprice_offset=offset,
            imbalance=imbalance,
            vwap=self._notional / self._total_volume if has_volume else None,
            total_volume=self._total_volume,
            buy_volume=self._buy_volume,
            buy_fraction=self._buy_volume / self._total_volume if has_volume else None,
            trade_count=self._trade_count,
            hawkes_intensity=intensity,
            hawkes_ratio=ratio,
            hawkes_regime=regime,
            book_timestamp=self._book_timestamp,
            last_trade_timestamp=self._last_trade_timestamp,
        )
