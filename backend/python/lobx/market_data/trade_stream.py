"""
Trade stream parser — @trade event handling.

Binance @trade event (raw JSON field mapping)
----------------------------------------------
{
  "e": "trade",        # event type
  "E": 1234567890123,  # event time (ms epoch)
  "s": "BTCUSDT",      # symbol
  "t": 12345,          # trade ID
  "p": "0.001",        # price (string — always parse with float())
  "q": "100",          # quantity (string — always parse with float())
  "T": 1234567890123,  # trade time (ms epoch, often == E)
  "m": true,           # is the buyer the market maker?
                       #   True  → seller is the taker (aggressor)
                       #   False → buyer  is the taker (aggressor)
  "M": true            # ignore (deprecated best match flag)
}

The `is_buyer_maker` field is crucial for order-flow analysis:
    * is_buyer_maker = True  → a sell market order hit a resting buy limit order
                                (seller was aggressive; "sell" tick)
    * is_buyer_maker = False → a buy market order hit a resting sell limit order
                                (buyer was aggressive; "buy" tick)

This convention is used in OFI (Order Flow Imbalance) computation and Hawkes
process calibration — you need to know the aggressor side, not just that a
trade happened.

TradeTick
---------
A frozen dataclass so it can be hashed, compared, and used in sets/dicts
without mutation risk.  `to_dict()` produces a plain dict for Parquet writes
without any import of pyarrow in this module (keeps trade parsing lightweight).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class TradeTick:
    """
    One trade event from Binance @trade stream.

    Attributes
    ----------
    event_time      : exchange timestamp in milliseconds (int64)
    trade_id        : exchange-assigned trade ID (int64, monotonically increasing)
    price           : trade price as a float (e.g., 67543.21)
    qty             : trade quantity as a float (e.g., 0.00142)
    is_buyer_maker  : True → seller was the taker; False → buyer was the taker
    symbol          : "BTCUSDT" etc.
    """

    event_time: int
    trade_id: int
    price: float
    qty: float
    is_buyer_maker: bool
    symbol: str

    # ── Derived helpers ───────────────────────────────────────────────────────

    @property
    def notional(self) -> float:
        """Notional value of this trade in quote currency (price × qty)."""
        return self.price * self.qty

    @property
    def taker_side(self) -> str:
        """
        The side of the *taker* (aggressor):
            "sell" if is_buyer_maker == True  (seller hit the bid)
            "buy"  if is_buyer_maker == False (buyer hit the ask)
        """
        return "sell" if self.is_buyer_maker else "buy"

    # ── Serialisation ─────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        """
        Convert to a plain dict compatible with RotatingParquetWriter.write().
        Uses dataclasses.asdict for automatic field traversal — adding new
        fields to this dataclass will automatically appear in Parquet output.
        """
        return asdict(self)

    def __repr__(self) -> str:
        return (
            f"<TradeTick {self.symbol} {self.taker_side.upper()} "
            f"px={self.price:.2f} qty={self.qty:.5f} "
            f"notional={self.notional:.2f} id={self.trade_id}>"
        )


# ── Parsing ───────────────────────────────────────────────────────────────────

def parse_trade_event(event: dict) -> TradeTick:
    """
    Parse one raw @trade event dict into a TradeTick.

    Price and quantity come from Binance as strings ("67543.21") — always
    convert with float(), never int(), because crypto quantities can have
    8 decimal places.

    Parameters
    ----------
    event : the `data` field from a combined-stream @trade message.

    Returns
    -------
    TradeTick (frozen — safe to cache, hash, and pass across async boundaries)

    Example raw event
    -----------------
    {
      "e": "trade", "E": 1722567890123, "s": "BTCUSDT",
      "t": 3895012, "p": "67543.21", "q": "0.00142",
      "T": 1722567890120, "m": False, "M": True
    }
    """
    return TradeTick(
        event_time=int(event["E"]),
        trade_id=int(event["t"]),
        price=float(event["p"]),
        qty=float(event["q"]),
        is_buyer_maker=bool(event["m"]),
        symbol=str(event["s"]),
    )
