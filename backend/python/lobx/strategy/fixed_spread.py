"""
strategy/fixed_spread.py — Fixed symmetric spread around mid-price.

This is the mandatory baseline strategy. Every AS result reported in the
Black Book must be compared against this. If AS doesn't beat fixed-spread
PnL or inventory control after a multi-hour session, the calibration is
wrong — not the comparison.

It is intentionally simple:
  - Quotes half_spread on each side of mid.
  - Ignores inventory (it will drift — that's the point).
  - Ignores volatility (its spread never adjusts — also the point).
"""
from __future__ import annotations

from lobx.strategy.base import MarketState, Quote, QuotePair, Strategy


class FixedSpreadStrategy(Strategy):
    """Quote a constant half-spread around the current mid-price.

    Parameters
    ----------
    half_spread : distance from mid to each quote price (in USD for BTC/USDT).
                  Default 0.5 → bid at mid-0.50, ask at mid+0.50.
    qty         : size of each side quote (fractional BTC).
    """

    def __init__(self, half_spread: float = 0.5, qty: float = 0.001) -> None:
        self.half_spread = half_spread
        self.qty = qty

    def on_tick(self, state: MarketState) -> QuotePair:
        return QuotePair(
            bid=Quote(price=round(state.mid_price - self.half_spread, 2), qty=self.qty),
            ask=Quote(price=round(state.mid_price + self.half_spread, 2), qty=self.qty),
        )
