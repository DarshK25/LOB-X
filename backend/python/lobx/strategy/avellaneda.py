"""
strategy/avellaneda.py — Avellaneda-Stoikov market-making strategy.

Wraps the existing math in lobx.models.avellaneda behind the Strategy ABC
so the runner can swap it in identically to FixedSpreadStrategy.

Function signatures (verified by scripts/inspect_as_module.py):
  reservation_price(mid, inventory, gamma, sigma, time_remaining) -> float
  half_spread(gamma, sigma, time_remaining, kappa) -> float

Parameters (gamma, kappa) are PLACEHOLDERS until Phase 3 calibrates them
from the collected trade Parquet data via Hawkes process fitting. The values
0.1 / 1.5 are a starting point, not a calibrated estimate — label them
clearly in any performance report.
"""
from __future__ import annotations

from lobx.strategy.base import MarketState, Quote, QuotePair, Strategy

# Real imports — names/args verified by scripts/inspect_as_module.py output.
from lobx.models.avellaneda.reservation_price import reservation_price
from lobx.models.avellaneda.optimal_spread import half_spread


class AvellanedaStoikovStrategy(Strategy):
    """Live market-making strategy using the Avellaneda-Stoikov model.

    On each tick:
      1. Compute time remaining in the session (elapsed / horizon).
      2. Call reservation_price(mid, inventory, gamma, sigma, time_remaining)
         to get the skewed mid that reduces inventory risk.
      3. Call half_spread(gamma, sigma, time_remaining, kappa) to get
         the distance from reservation price to each quote.
      4. Return QuotePair(bid=r-hs, ask=r+hs).

    Key observable: after a shadow fill (inventory ≠ 0), the next tick's
    reservation price should visibly shift AWAY from mid in the direction
    that would reduce inventory. If it doesn't, either inventory tracking
    or volatility feeding is broken.

    Parameters
    ----------
    gamma           : risk-aversion coefficient (>0). Higher → tighter
                      quotes when inventory is large.
    kappa           : order-flow intensity. Higher → narrower spread.
                      Placeholder until Phase 3 Hawkes calibration.
    horizon_seconds : session length in seconds. Controls how fast quotes
                      collapse toward mid as time runs out.
    qty             : quote size in BTC.
    min_spread      : minimum total spread enforced regardless of model
                      output (prevents bid >= ask when vol is near 0).
    """

    def __init__(
        self,
        gamma:           float = 0.1,
        kappa:           float = 1.5,
        horizon_seconds: float = 300.0,
        qty:             float = 0.001,
        min_spread:      float = 0.02,
    ) -> None:
        self.gamma           = gamma
        self.kappa           = kappa
        self.horizon_seconds = horizon_seconds
        self.qty             = qty
        self.min_spread      = min_spread
        self._start_time_ms: int | None = None

    def on_tick(self, state: MarketState) -> QuotePair:
        # Record the first tick's timestamp as session start.
        if self._start_time_ms is None:
            self._start_time_ms = state.timestamp

        elapsed_s     = (state.timestamp - self._start_time_ms) / 1_000.0
        time_remaining = max(self.horizon_seconds - elapsed_s, 0.01)

        # Use zero volatility guard: when vol estimator hasn't warmed up yet,
        # fall back to a small floor to keep the model well-behaved.
        sigma = max(state.volatility, 1e-8)

        # Avellaneda-Stoikov reservation price — skewed by inventory × risk.
        r = reservation_price(
            mid=state.mid_price,
            inventory=state.inventory,
            gamma=self.gamma,
            sigma=sigma,
            time_remaining=time_remaining,
        )

        # Optimal half-spread from AS formula.
        hs = half_spread(
            gamma=self.gamma,
            sigma=sigma,
            time_remaining=time_remaining,
            kappa=self.kappa,
        )

        # Enforce minimum spread to prevent degenerate quotes.
        hs = max(hs, self.min_spread / 2.0)

        bid_price = round(r - hs, 2)
        ask_price = round(r + hs, 2)

        # Hard safety: bid must always be strictly below ask.
        if bid_price >= ask_price:
            ask_price = round(bid_price + self.min_spread, 2)

        return QuotePair(
            bid=Quote(price=bid_price, qty=self.qty),
            ask=Quote(price=ask_price, qty=self.qty),
        )
