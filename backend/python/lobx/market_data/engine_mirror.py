"""
Engine Mirror Bridge — feeds live Binance depth into the C++ OrderBook.

Purpose
-------
Your C++ OrderBook (lobx_cpp.OrderBook) stores individual resting Order objects
at tick-precision integer prices.  The Binance public feed gives you float prices
and aggregate quantities per level — no individual order IDs.

This bridge translates between the two representations by treating each
(side, price) pair as ONE synthetic resting order whose qty equals the total
resting quantity at that level.  When Binance says "bid at 67543.00 = 1.234 BTC",
we cancel any existing synthetic order at that price and insert a new one.

Why bother? Two reasons:
    1. Proof of correctness: after mirroring, your engine's best_bid() /
       best_ask() / mid_price() should match LocalOrderBook's — if they don't,
       there's a bug in either the bridge or the engine.
    2. Strategy substrate: AvellanedaStoikovMM.step() calls self.book.mid_price()
       on a lobx_cpp.OrderBook.  Once Phase 2 wires the strategy to the live book,
       the market maker quotes against real market state, not a synthetic one.

Synthetic order IDs
-------------------
The C++ OrderBook.cancel() requires a numeric OrderId (uint64).  We need a
deterministic mapping from (side, price) → uint64 that:
    * Always produces the same ID for the same (side, price) pair, so "cancel
      then re-insert" logic works across calls.
    * Never collides between bid and ask sides, or between different prices.
    * Fits in uint64 without truncation for any realistic BTC/USDT price.

Algorithm: pack the (side, scaled_price) tuple into a uint64 directly.
    * Prices from Binance are floats like 67543.21.  We scale by 1e8
      (8 decimal places — the maximum Binance ever uses) and take the integer:
          67543.21 × 1e8 = 6_754_321_000_000  (~43 bits, fits in uint64)
    * Side bit: 0 for bid, 1 for ask, stored in bit 63 (the highest bit).
    * Result: unique, deterministic, no hash, no collision, no allocation.

Tick conversion
---------------
C++ Price = int64_t ticks.  We define 1 tick = 0.01 USD (one cent) for
BTC/USDT, so:
    price_ticks = round(price_float * TICKS_PER_UNIT)

TICKS_PER_UNIT = 100 gives cent resolution.  For coins priced at fractions of
a cent (e.g., SHIB at $0.00001234), use TICKS_PER_UNIT = 10_000_000 or similar.
Adjust this constant before connecting to a different symbol.

IMPORTANT: engine_mirror.py requires lobx_cpp to be built and importable.
If you're running the collector WITHOUT the C++ engine compiled (e.g., pure
data-collection mode on a machine without a C++ toolchain), set
ENABLE_ENGINE_MIRROR = False in collector.py — the data pipeline still works.
"""
from __future__ import annotations

# NOTE: lobx_cpp is imported lazily inside EngineMirrorBridge.__init__ so that
# the pure-Python ID-encoding helpers (_synthetic_id, price_to_ticks) can be
# imported and tested without the C++ extension being compiled.

# ── Tick conversion ───────────────────────────────────────────────────────────
# 1 cent precision: multiply float price by 100 to get integer ticks.
# BTC/USDT minimum tick size on Binance is $0.01, so this is exact.
TICKS_PER_UNIT: int = 100


def price_to_ticks(price: float) -> int:
    """
    Convert a float Binance price to an integer tick value.

    Example: 67543.21 → 6_754_321 (with TICKS_PER_UNIT = 100)

    The C++ Order struct stores Price as int64_t — using integers avoids
    floating-point comparison bugs in the matching engine's price checks.
    """
    return round(price * TICKS_PER_UNIT)


# ── Synthetic order ID encoding ───────────────────────────────────────────────
#
# Bit layout of the synthetic uint64 order ID:
#
#    bit 63      : side  (0 = bid, 1 = ask)
#    bits 0–62   : scaled price  (price_float × 1e8, truncated to 63 bits)
#
# Maximum price representable: 2^63 / 1e8 ≈ $92 million — safely above any
# realistic BTC price.

_SIDE_BIT_ASK: int = 1 << 63


def _synthetic_id(side: str, price: float) -> int:
    """
    Encode (side, price) into a deterministic uint64 synthetic order ID.

    Parameters
    ----------
    side  : "bid" or "ask"
    price : float price from Binance

    Returns
    -------
    A uint64-compatible non-negative Python int.
    """
    # Scale price to an integer with 8 decimal places of precision.
    # This handles all Binance price formats without truncation.
    scaled = int(round(price * 1e8)) & 0x7FFF_FFFF_FFFF_FFFF  # mask to 63 bits
    if side == "ask":
        return scaled | _SIDE_BIT_ASK
    return scaled


# ── Bridge ────────────────────────────────────────────────────────────────────

class EngineMirrorBridge:
    """
    Maintains a lobx_cpp.OrderBook that mirrors the Binance order book.

    Each price level on each side is represented as ONE synthetic resting order:
        OrderBook  →  { bid@67543.00: Order(id=..., price=6754300, qty=123, is_buy=True) }

    When Binance says "bid@67543.00 qty changed to 1.50":
        1. cancel(synthetic_id("bid", 67543.00))   ← remove old level
        2. add_limit_order(Order(..., qty=150, ...)) ← insert new level

    When qty == 0:
        1. cancel(synthetic_id("bid", 67543.00))   ← level removed from book
        (no insert)

    The OrderBook's internal matching logic is NOT triggered here because we
    only ever insert bids below the current best ask and asks above the current
    best bid (the real exchange enforces this — crossed books don't exist in
    live data after sync).

    Attributes
    ----------
    book : the underlying lobx_cpp.OrderBook (read this from strategies)
    """

    def __init__(self) -> None:
        try:
            import lobx_cpp as _cpp  # lazy — only needed when the bridge is instantiated
        except ModuleNotFoundError as exc:
            raise ImportError(
                "EngineMirrorBridge requires the compiled C++ extension. "
                "Build it first: cd backend/cpp && cmake -B build && cmake --build build"
            ) from exc
        self._cpp = _cpp
        self.book = _cpp.OrderBook()

    def apply_depth_event(self, event: dict) -> None:
        """
        Apply one diff-depth event to the C++ engine book.

        Call this ONLY after DepthStreamManager reports is_synced == True.
        Applying events before sync makes the engine's state diverge from
        the real book.

        Parameters
        ----------
        event : the raw @depth event dict (with "b" and "a" fields)
        """
        for price_str, qty_str in event.get("b", []):
            self._apply_level("bid", float(price_str), float(qty_str))
        for price_str, qty_str in event.get("a", []):
            self._apply_level("ask", float(price_str), float(qty_str))

    def _apply_level(self, side: str, price: float, qty: float) -> None:
        """
        Update one price level on one side of the engine book.

        Step 1 — Cancel the existing synthetic order (if any).
                 book.cancel() is a no-op if the order_id doesn't exist — safe
                 to call unconditionally.
        Step 2 — If qty > 0, insert a new synthetic order at that level.
                 qty is in BTC (float); we need integer lots for the engine.
                 Scale: 1 lot = 0.00001 BTC (Binance minimum lot size = 5 lots
                 of 0.00001 BTC for BTCUSDT).  So qty_lots = round(qty * 1e5).

        Parameters
        ----------
        side  : "bid" or "ask"
        price : float price from the exchange
        qty   : float quantity from the exchange (0 = level removed)
        """
        oid = _synthetic_id(side, price)

        # Always cancel first — clean slate for this level.
        self.book.cancel(oid)

        if qty <= 0.0:
            return  # level removed; we're done

        # Convert price to integer ticks for the C++ engine.
        price_ticks = price_to_ticks(price)

        # Convert quantity to integer lots.
        # BTC/USDT step size = 0.00001 BTC → 1 lot.
        # Example: qty=1.234 BTC → qty_lots = 123400
        qty_lots = max(1, round(qty * 1e5))

        is_buy = side == "bid"

        # lobx_cpp.Order(id, price, qty, is_buy, tif=GTC)
        # is_buy maps to Side::Buy / Side::Sell internally in the C++ Order ctor.
        order = self._cpp.Order(oid, price_ticks, qty_lots, is_buy)
        self.book.add_limit_order(order)

    # ── Diagnostic helpers ────────────────────────────────────────────────────

    def best_bid_price(self) -> float | None:
        """Best bid price in float USD (converting from ticks), or None."""
        t = self.book.best_bid()
        return t / TICKS_PER_UNIT if t is not None else None

    def best_ask_price(self) -> float | None:
        """Best ask price in float USD (converting from ticks), or None."""
        t = self.book.best_ask()
        return t / TICKS_PER_UNIT if t is not None else None

    def mid_price(self) -> float | None:
        """Mid price in float USD, or None."""
        m = self.book.mid_price()
        return m / TICKS_PER_UNIT if m is not None else None

    def spread(self) -> float | None:
        """Spread in float USD, or None."""
        s = self.book.spread()
        return s / TICKS_PER_UNIT if s is not None else None
