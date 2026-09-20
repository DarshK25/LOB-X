"""Enhanced AS strategy using measured microprice and L1 imbalance signals."""
from __future__ import annotations

from lobx.models.avellaneda.model import calculate_quote
from lobx.strategy.base import MarketState, Quote, QuotePair, Strategy


class EnhancedAvellanedaStoikovStrategy(Strategy):
    """Classical AS plus explicit microprice/imbalance reference adjustment.

    The adjustment is reported in ``last_quote_audit`` so replay can measure
    whether it improves outcomes; it is not presented as calibrated alpha.
    """

    def __init__(
        self,
        gamma: float = 0.1,
        kappa: float = 1.5,
        horizon_seconds: float = 300.0,
        qty: float = 0.001,
        min_spread: float = 0.02,
        signal_weight: float = 0.25,
    ) -> None:
        self.gamma = gamma
        self.kappa = kappa
        self.horizon_seconds = horizon_seconds
        self.qty = qty
        self.min_spread = min_spread
        self.signal_weight = signal_weight
        self._start_time_ms: int | None = None
        self.last_quote_audit: dict[str, float] = {}

    def on_tick(self, state: MarketState) -> QuotePair:
        if self._start_time_ms is None:
            self._start_time_ms = state.timestamp
        elapsed = (state.timestamp - self._start_time_ms) / 1_000.0
        remaining = max(self.horizon_seconds - elapsed, 0.01)
        sigma = max(state.volatility, 1e-8)
        quote = calculate_quote(
            mid=state.mid_price + self.signal_weight * state.microprice_deviation,
            inventory=state.inventory,
            gamma=self.gamma,
            sigma=sigma,
            time_remaining=remaining,
            kappa=self.kappa,
        )
        half = max(quote.full_spread / 2.0, self.min_spread / 2.0)
        bid = round(quote.reservation_price - half, 2)
        ask = round(quote.reservation_price + half, 2)
        if bid >= ask:
            ask = round(bid + self.min_spread, 2)
        self.last_quote_audit = {
            "gamma": self.gamma,
            "kappa": self.kappa,
            "sigma": sigma,
            "time_remaining_seconds": remaining,
            "microprice_deviation": state.microprice_deviation,
            "imbalance": state.imbalance,
            "signal_adjustment": self.signal_weight * state.microprice_deviation,
        }
        return QuotePair(Quote(bid, self.qty), Quote(ask, self.qty))
