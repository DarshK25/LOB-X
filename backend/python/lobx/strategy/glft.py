"""Calibrated GLFT market maker with Hawkes-aware spreads and a fair-value skew.

Pipeline per decision:
  1. fair = mid + alpha(state)            short-horizon fair value (fitted in-sample)
  2. GLFT depths from calibrated A, k, live sigma and inventory in lots
  3. widen both depths when the Hawkes intensity ratio is above 1 (bursts of
     market orders are when passive quotes get picked off)
  4. optional fee-aware filter: drop a side whose expected edge versus fair
     value does not cover the maker fee

All parameters are explicit and auditable through ``last_quote_audit``.
"""
from __future__ import annotations

from dataclasses import dataclass

from lobx.models.glft import glft_coefficients, glft_depths
from lobx.strategy.base import MarketState, QuotePair, Strategy, passive_pair


@dataclass(frozen=True)
class FairValueModel:
    """Linear predictor of the mid change over a short horizon (USD).

    alpha = b_micro * microprice_deviation + b_flow * flow_imbalance * sigma
            + b_obi * imbalance * sigma
    Coefficients come from an in-sample least-squares fit (see
    ``lobx.analytics.calibration.fit_fair_value``).
    """

    b_micro: float = 0.0
    b_flow: float = 0.0
    b_obi: float = 0.0
    b_volume: float = 0.0

    def alpha(self, state: MarketState) -> float:
        return (self.b_micro * state.microprice_deviation
                + self.b_flow * state.flow_imbalance * state.sigma
                + self.b_obi * state.imbalance * state.sigma
                + self.b_volume * state.signed_volume_imbalance * state.sigma)


class GLFTStrategy(Strategy):
    def __init__(
        self,
        gamma: float,
        A: float,
        k: float,
        qty: float = 0.001,
        hawkes_widen: float = 0.0,
        hawkes_pause_ratio: float | None = None,
        fair_value: FairValueModel | None = None,
        maker_fee: float = 0.0,
        fee_aware: bool = False,
        max_depth: float | None = None,
        sigma_reference: float | None = None,
        sigma_power: float = 0.0,
        asymmetric_hawkes: bool = False,
        directional_pause: bool = False,
        toxicity_widen: float = 0.0,
        queue_widen: float = 0.0,
        fee_floor: bool = False,
        edge_margin_bps: float = 0.0,
        label: str | None = None,
    ) -> None:
        if qty <= 0:
            raise ValueError("qty must be positive")
        if min(toxicity_widen, queue_widen, edge_margin_bps) < 0:
            raise ValueError("widening and edge-margin parameters must be non-negative")
        self.gamma, self.A, self.k = gamma, A, k
        self.qty = qty
        self.coeffs = glft_coefficients(gamma, k, A)
        self.hawkes_widen = hawkes_widen
        self.hawkes_pause_ratio = hawkes_pause_ratio
        self.fair_value = fair_value or FairValueModel()
        self.maker_fee = maker_fee
        self.fee_aware = fee_aware
        self.max_depth = max_depth
        self.sigma_reference = sigma_reference
        self.sigma_power = sigma_power
        self.asymmetric_hawkes = asymmetric_hawkes
        self.directional_pause = directional_pause
        self.toxicity_widen = toxicity_widen
        self.queue_widen = queue_widen
        self.fee_floor = fee_floor
        self.edge_margin_bps = edge_margin_bps
        self.label = label
        self.last_quote_audit: dict[str, float] = {}

    def on_tick(self, state: MarketState, position: float) -> QuotePair:
        q = self.lots(position)
        bid_depth, ask_depth, half_spread, skew = glft_depths(state.sigma, q, self.coeffs)
        excess = max(0.0, state.hawkes_ratio - 1.0)
        if self.asymmetric_hawkes:
            bid_widen = 1.0 + self.hawkes_widen * excess * (1.0 - state.flow_imbalance)
            ask_widen = 1.0 + self.hawkes_widen * excess * (1.0 + state.flow_imbalance)
        else:
            bid_widen = ask_widen = 1.0 + self.hawkes_widen * excess
        if self.sigma_reference and self.sigma_power:
            vol_scale = max(0.5, min(3.0, (state.sigma / self.sigma_reference) ** self.sigma_power))
        else:
            vol_scale = 1.0
        toxicity_scale = 1.0 + self.toxicity_widen * state.volume_toxicity
        bid_queue_scale = 1.0 + self.queue_widen * max(0.0, state.imbalance)
        ask_queue_scale = 1.0 + self.queue_widen * max(0.0, -state.imbalance)
        bid_depth *= bid_widen * vol_scale * toxicity_scale * bid_queue_scale
        ask_depth *= ask_widen * vol_scale * toxicity_scale * ask_queue_scale
        if self.max_depth is not None:
            bid_depth = min(bid_depth, self.max_depth)
            ask_depth = min(ask_depth, self.max_depth)
        alpha = self.fair_value.alpha(state)
        fair = state.mid + alpha
        bid = fair - bid_depth
        ask = fair + ask_depth
        fee_cost = self.maker_fee * state.mid
        if self.fee_floor:
            required_edge = max(0.0, fee_cost + self.edge_margin_bps * state.mid / 1e4)
            bid = min(bid, fair - required_edge)
            ask = max(ask, fair + required_edge)

        quote_bid = quote_ask = True
        if self.hawkes_pause_ratio is not None and state.hawkes_ratio >= self.hawkes_pause_ratio:
            if self.directional_pause:
                # Buy bursts are toxic to asks; sell bursts are toxic to bids.
                # Inventory-reducing quotes remain allowed.
                quote_bid = state.flow_imbalance >= 0.0 or q < 0
                quote_ask = state.flow_imbalance <= 0.0 or q > 0
            else:
                # Burst: only quote the side that reduces inventory.
                quote_bid = q < 0
                quote_ask = q > 0
        if self.fee_aware:
            pair = passive_pair(state, bid, ask, self.qty, self.qty)
            if pair.bid is not None and fair - pair.bid.price < fee_cost:
                quote_bid = False
            if pair.ask is not None and pair.ask.price - fair < fee_cost:
                quote_ask = False

        self.last_quote_audit = {
            "fair_value": fair,
            "alpha": alpha,
            "half_spread": half_spread,
            "inventory_skew": skew * q,
            "bid_widen": bid_widen,
            "ask_widen": ask_widen,
            "vol_scale": vol_scale,
            "toxicity_scale": toxicity_scale,
            "volume_imbalance": state.signed_volume_imbalance,
            "volume_toxicity": state.volume_toxicity,
            "inventory_lots": q,
            "sigma": state.sigma,
            "hawkes_ratio": state.hawkes_ratio,
        }
        return passive_pair(state, bid if quote_bid else None, ask if quote_ask else None,
                            self.qty, self.qty)
