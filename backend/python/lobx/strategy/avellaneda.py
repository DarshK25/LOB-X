"""
strategy/avellaneda.py — Canonical Avellaneda-Stoikov market-making strategy.

Single canonical implementation of the Avellaneda-Stoikov model with optional
quantitative enhancements (Hawkes trade intensity excitation, microprice skew,
and toxicity filtering) configurable directly on the strategy class.

Function signatures:
  calculate_quote(mid, inventory, gamma, sigma, time_remaining, kappa) -> ASQuote
"""
from __future__ import annotations

import math
from lobx.strategy.base import MarketState, Quote, QuotePair, Strategy
from lobx.models.avellaneda.model import calculate_quote
from lobx.models.hawkes.intensity import HawkesIntensity


class AvellanedaStoikovStrategy(Strategy):
    """Live and backtest market-making strategy using the Avellaneda-Stoikov model.

    On each tick:
      1. Compute time remaining in the session (horizon_seconds - elapsed_s).
      2. Optionally compute Hawkes intensity adjustment to kappa if use_hawkes is True.
      3. Call calculate_quote(...) for canonical AS reservation price and optimal spread.
      4. Optionally adjust reservation price with microprice deviation if use_microprice is True.
      5. Optionally apply toxicity/markout filter to widen quotes if use_toxicity is True.
      6. Return QuotePair(bid=r-hs, ask=r+hs).

    Parameters
    ----------
    gamma           : risk-aversion coefficient (>0). Higher -> tighter/more skewed quotes.
    kappa           : base order-flow intensity. Higher -> narrower spread.
    horizon_seconds : session length in seconds.
    qty             : quote size in BTC.
    min_spread      : minimum total spread enforced.
    use_hawkes      : enable Hawkes process trade-intensity excitation. Spreads widen when trade arrival spikes.
    use_microprice  : enable microprice & order imbalance reservation price skew.
    use_toxicity    : enable adverse-selection filter based on short-term price velocity.
    """

    def __init__(
        self,
        gamma:           float = 0.1,
        kappa:           float = 1.5,
        horizon_seconds: float = 300.0,
        qty:             float = 0.001,
        min_spread:      float = 0.02,
        use_hawkes:      bool = False,
        use_microprice:  bool = False,
        use_toxicity:    bool = False,
        hawkes_mu:       float = 1.0,
        hawkes_alpha:    float = 1.5,
        hawkes_beta:     float = 2.0,
    ) -> None:
        self.gamma           = gamma
        self.kappa           = kappa
        self.horizon_seconds = horizon_seconds
        self.qty             = qty
        self.min_spread      = min_spread
        self.use_hawkes      = use_hawkes
        self.use_microprice  = use_microprice
        self.use_toxicity    = use_toxicity

        self._start_time_ms: int | None = None
        self._hawkes: HawkesIntensity | None = (
            HawkesIntensity(mu=hawkes_mu, alpha=hawkes_alpha, beta=hawkes_beta)
            if use_hawkes else None
        )
        self._recent_mids: list[tuple[int, float]] = []
        self.last_quote_audit: dict[str, float | str | bool] = {}

    def on_tick(self, state: MarketState) -> QuotePair:
        if self._start_time_ms is None:
            self._start_time_ms = state.timestamp

        elapsed_s     = (state.timestamp - self._start_time_ms) / 1_000.0
        time_remaining = max(self.horizon_seconds - elapsed_s, 0.01)
        sigma = max(state.volatility, 1e-8)

        # Track recent mids for toxicity/velocity calculation
        self._recent_mids.append((state.timestamp, state.mid_price))
        cutoff = state.timestamp - 5_000  # 5-second window
        self._recent_mids = [m for m in self._recent_mids if m[0] >= cutoff]

        # 1. Hawkes Intensity Adjustment
        # NOTE: Hawkes tracks actual trade arrivals, not depth updates.
        # In backtest, MarketState.trade_price is populated when a trade occurred.
        # In live mode without trade stream, Hawkes remains at baseline.
        effective_kappa = self.kappa
        hawkes_intensity = 1.0
        if self._hawkes is not None:
            # Track actual trade events, not depth updates
            if state.trade_price is not None and state.timestamp is not None:
                # Update Hawkes with trade event timestamp (in seconds)
                self._hawkes.update(state.timestamp / 1000.0)
                hawkes_intensity = self._hawkes.intensity
            elif state.timestamp is not None:
                # No trade at this timestamp, evaluate current intensity
                hawkes_intensity = self._hawkes.evaluate(state.timestamp / 1000.0)
            
            # Excitation increases trade intensity -> widen effective spread by dampening effective_kappa
            effective_kappa = max(self.kappa / math.sqrt(hawkes_intensity), 0.01)

        # 2. Canonical AS Model Calculation
        model_quote = calculate_quote(
            mid=state.mid_price,
            inventory=state.inventory,
            gamma=self.gamma,
            sigma=sigma,
            time_remaining=time_remaining,
            kappa=effective_kappa,
        )
        r = model_quote.reservation_price
        hs = model_quote.full_spread / 2.0

        # 3. Microprice & Order Imbalance Skew
        micro_skew = 0.0
        if self.use_microprice and state.microprice is not None:
            micro_dev = state.microprice - state.mid_price
            # Shift reservation price toward microprice
            micro_skew = 0.5 * micro_dev
            r += micro_skew

        # 4. Toxicity / Adverse Selection Filter
        toxic_bid_penalty = 0.0
        toxic_ask_penalty = 0.0
        if self.use_toxicity and len(self._recent_mids) >= 2:
            dt = (self._recent_mids[-1][0] - self._recent_mids[0][0]) / 1000.0
            if dt > 0.5:
                price_velocity = (self._recent_mids[-1][1] - self._recent_mids[0][1]) / dt
                if price_velocity < -2.0:  # Fast downward movement -> avoid buying
                    toxic_bid_penalty = abs(price_velocity) * 0.1
                elif price_velocity > 2.0:  # Fast upward movement -> avoid selling
                    toxic_ask_penalty = abs(price_velocity) * 0.1

        # Final quote pricing with min_spread guard
        hs = max(hs, self.min_spread / 2.0)
        bid_price = round(r - hs - toxic_bid_penalty, 2)
        ask_price = round(r + hs + toxic_ask_penalty, 2)

        if bid_price >= ask_price:
            ask_price = round(bid_price + self.min_spread, 2)

        self.last_quote_audit = {
            "strategy_type": "AS_Enhanced" if (self.use_hawkes or self.use_microprice or self.use_toxicity) else "AS_Canonical",
            "gamma": self.gamma,
            "kappa": self.kappa,
            "effective_kappa": effective_kappa,
            "hawkes_intensity": hawkes_intensity,
            "sigma": sigma,
            "time_remaining_seconds": time_remaining,
            "reservation_price": r,
            "model_full_spread": model_quote.full_spread,
            "applied_half_spread": hs,
            "micro_skew": micro_skew,
            "toxic_bid_penalty": toxic_bid_penalty,
            "toxic_ask_penalty": toxic_ask_penalty,
            "quote_bid": bid_price,
            "quote_ask": ask_price,
            "inventory_skew": r - state.mid_price,
        }

        return QuotePair(
            bid=Quote(price=bid_price, qty=self.qty),
            ask=Quote(price=ask_price, qty=self.qty),
        )
