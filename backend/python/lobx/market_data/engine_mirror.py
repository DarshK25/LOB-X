"""Mirror the exchange L2 book into the C++ ``lobx_cpp.OrderBook``.

Each (side, price) level is one synthetic resting order whose quantity is
the level's aggregate size. Used as a correctness check: after every
update the C++ book's best bid/ask must equal ``LocalOrderBook``'s.

The C++ book is a matching engine, so an insert that crosses the opposite
side would execute a fake trade. To keep the mirror exact:
  * removals and size changes of an event are applied before inserts
  * a level that would cross is preceded by removing the stale opposite
    levels it crosses (they are always stale: the exchange book never crosses)
Prices are integer ticks (``ticks_per_unit``), sizes integer lots
(``lots_per_unit``).
"""
from __future__ import annotations

_SIDE_BIT_ASK = 1 << 63


def _synthetic_id(side: str, price_ticks: int) -> int:
    """Deterministic uint64 id per (side, price): bit 63 = side, rest = price."""
    oid = price_ticks & 0x7FFF_FFFF_FFFF_FFFF
    return oid | _SIDE_BIT_ASK if side == "ask" else oid


class EngineMirrorBridge:
    def __init__(self, ticks_per_unit: int = 100, lots_per_unit: int = 100_000) -> None:
        try:
            import lobx_cpp as _cpp
        except ModuleNotFoundError as exc:
            raise ImportError("EngineMirrorBridge needs the compiled lobx_cpp extension "
                              "(python scripts/setup_cpp_build.py)") from exc
        self._cpp = _cpp
        self.ticks_per_unit = ticks_per_unit
        self.lots_per_unit = lots_per_unit
        self.book = _cpp.OrderBook()
        self._levels: dict[str, dict[int, int]] = {"bid": {}, "ask": {}}
        self.trades_generated = 0

    def price_to_ticks(self, price: float) -> int:
        return round(price * self.ticks_per_unit)

    def load_snapshot(self, bids, asks) -> None:
        """Reset to a REST snapshot ([[price, qty], ...] as strings or floats)."""
        self.book = self._cpp.OrderBook()
        self._levels = {"bid": {}, "ask": {}}
        self._apply(list(bids), list(asks))

    def apply_depth_event(self, event: dict) -> None:
        """Apply one diff-depth event (only after the stream is in sync)."""
        self._apply(event.get("b", []), event.get("a", []))

    def _apply(self, bids, asks) -> None:
        updates = [("bid", self.price_to_ticks(float(p)), float(q)) for p, q in bids]
        updates += [("ask", self.price_to_ticks(float(p)), float(q)) for p, q in asks]
        inserts = []
        for side, px, qty in updates:
            if px in self._levels[side]:
                self._remove(side, px)
            if qty > 0:
                inserts.append((side, px, qty))
        for side, px, qty in inserts:
            self._clear_crossed(side, px)
            lots = max(1, round(qty * self.lots_per_unit))
            trades = self.book.add_limit_order(
                self._cpp.Order(_synthetic_id(side, px), px, lots, side == "bid"))
            self.trades_generated += len(trades)
            self._levels[side][px] = lots

    def _remove(self, side: str, px: int) -> None:
        self.book.cancel(_synthetic_id(side, px))
        del self._levels[side][px]

    def _clear_crossed(self, side: str, px: int) -> None:
        other = "ask" if side == "bid" else "bid"
        crossed = [p for p in self._levels[other] if (p <= px if side == "bid" else p >= px)]
        for p in crossed:
            self._remove(other, p)

    def best_bid_price(self) -> float | None:
        t = self.book.best_bid()
        return t / self.ticks_per_unit if t is not None else None

    def best_ask_price(self) -> float | None:
        t = self.book.best_ask()
        return t / self.ticks_per_unit if t is not None else None

    def mid_price(self) -> float | None:
        m = self.book.mid_price()
        return m / self.ticks_per_unit if m is not None else None

    def spread(self) -> float | None:
        s = self.book.spread()
        return s / self.ticks_per_unit if s is not None else None
