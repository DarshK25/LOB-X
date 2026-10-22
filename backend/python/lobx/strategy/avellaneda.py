"""Avellaneda-Stoikov market maker with a rolling horizon.

The original model has a terminal time T. A 24/7 market has none, so the
horizon is held constant at ``horizon_s`` (a rolling window): inventory risk is
always priced over the next ``horizon_s`` seconds. Inventory enters in lots
(position / order size), sigma in USD/sqrt(s), gamma and kappa in 1/USD.
"""
from __future__ import annotations

from lobx.models.avellaneda.model import calculate_quote
from lobx.strategy.base import MarketState, QuotePair, Strategy, passive_pair


class AvellanedaStoikovStrategy(Strategy):
    def __init__(self, gamma: float = 0.01, kappa: float = 5.0, horizon_s: float = 60.0,
                 qty: float = 0.001, label: str | None = None) -> None:
        if gamma <= 0 or kappa <= 0 or horizon_s <= 0 or qty <= 0:
            raise ValueError("gamma, kappa, horizon_s and qty must be positive")
        self.gamma = gamma
        self.kappa = kappa
        self.horizon_s = horizon_s
        self.qty = qty
        self.label = label
        self.last_quote_audit: dict[str, float] = {}

    def on_tick(self, state: MarketState, position: float) -> QuotePair:
        q = self.lots(position)
        quote = calculate_quote(state.mid, q, self.gamma, state.sigma, self.horizon_s, self.kappa)
        self.last_quote_audit = {
            "reservation_price": quote.reservation_price,
            "model_full_spread": quote.full_spread,
            "inventory_skew": quote.reservation_price - state.mid,
            "inventory_lots": q,
            "sigma": state.sigma,
        }
        return passive_pair(state, quote.bid, quote.ask, self.qty, self.qty)
