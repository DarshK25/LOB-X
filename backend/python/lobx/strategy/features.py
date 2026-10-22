"""Online market features shared by live trading, the simulator and backtests.

One ``FeatureEngine`` consumes top-of-book updates and trades and produces the
``MarketState`` every strategy sees: volatility in USD/sqrt(s), calibrated
Hawkes intensities for buy and sell market orders, and order-flow imbalance.
Feeding the same engine from the live collector and from historical files is
what keeps live paper results comparable to backtests.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np

from lobx.analytics.volatility import EWMAVolatility
from lobx.models.hawkes.calibration import calibrate as hawkes_mle
from lobx.models.hawkes.intensity import HawkesIntensity, regime
from lobx.strategy.base import MarketState


@dataclass(frozen=True)
class HawkesParams:
    mu: float
    alpha: float
    beta: float

    @classmethod
    def from_dict(cls, d: dict) -> "HawkesParams":
        return cls(mu=float(d["mu"]), alpha=float(d["alpha"]), beta=float(d["beta"]))


# Used only until a calibration is available (live warm-up). Documented as
# uncalibrated in the dashboard.
DEFAULT_HAWKES = HawkesParams(mu=0.5, alpha=0.8, beta=2.0)


class FeatureEngine:
    def __init__(
        self,
        tick_size: float,
        hawkes_buy: HawkesParams = DEFAULT_HAWKES,
        hawkes_sell: HawkesParams = DEFAULT_HAWKES,
        sigma_halflife_s: float = 120.0,
        sigma_seed: float | None = None,
        calibrated: bool = False,
        event_history: int = 0,
        volume_signal_s: int = 5,
        toxicity_buckets: int = 30,
    ) -> None:
        self.tick_size = tick_size
        self.vol = EWMAVolatility(sample_s=1.0, halflife_s=sigma_halflife_s)
        if sigma_seed:
            self.vol.seed(sigma_seed)
        # Market-order times per side (key: is_buyer_maker), kept only when
        # online refitting is wanted (live mode).
        self._events: dict[bool, deque[float]] | None = (
            {False: deque(maxlen=event_history), True: deque(maxlen=event_history)}
            if event_history else None)
        self.calibrated = calibrated
        self.set_hawkes(hawkes_buy, hawkes_sell, calibrated)
        self.bid = self.ask = 0.0
        self.bid_qty = self.ask_qty = 0.0
        self.last_ts = 0
        self._last_order_key: tuple[int, bool] | None = None
        self.volume_signal_s = max(1, volume_signal_s)
        self._volume_buckets: deque[list[float]] = deque(maxlen=max(2, toxicity_buckets))

    def set_hawkes(self, buy: HawkesParams, sell: HawkesParams, calibrated: bool = True) -> None:
        self.hawkes_buy = HawkesIntensity(buy.mu, buy.alpha, buy.beta)
        self.hawkes_sell = HawkesIntensity(sell.mu, sell.alpha, sell.beta)
        self.calibrated = calibrated
        if self._events:
            # Rebuild current excitation from recent events so a refit does
            # not reset the intensity to its baseline.
            for side, proc in ((False, self.hawkes_buy), (True, self.hawkes_sell)):
                for t in self._events[side]:
                    if t >= self.last_ts / 1000.0 - 10.0 / proc.beta:
                        proc.update(t)

    def recent_events(self) -> dict[bool, np.ndarray] | None:
        if self._events is None:
            return None
        return {side: np.fromiter(q, dtype=float) for side, q in self._events.items()}

    @staticmethod
    def fit_hawkes(events: dict[bool, np.ndarray], min_events: int = 300
                   ) -> tuple[HawkesParams, HawkesParams] | None:
        """MLE on a snapshot of ``recent_events()`` (safe to run in a worker thread)."""
        out = []
        for side in (False, True):
            ev = events[side]
            if len(ev) < min_events:
                return None
            fit = hawkes_mle(ev - ev[0], T=float(ev[-1] - ev[0]) or None, n_starts=2)
            out.append(HawkesParams(fit["mu"], fit["alpha"], fit["beta"]))
        return out[0], out[1]

    @property
    def ready(self) -> bool:
        return self.bid > 0 and self.ask > self.bid

    def on_book(self, ts: int, bid: float, bid_qty: float, ask: float, ask_qty: float) -> None:
        self.bid, self.bid_qty, self.ask, self.ask_qty = bid, bid_qty, ask, ask_qty
        self.last_ts = ts
        if ask > bid > 0:
            self.vol.update(ts, 0.5 * (bid + ask))

    def on_trade(self, ts: int, price: float, qty: float, is_buyer_maker: bool) -> None:
        # Volume signals use every print: one aggressive order can execute
        # across levels and its full size matters even though Hawkes counts the
        # order only once.
        sec = ts // 1000
        if not self._volume_buckets or self._volume_buckets[-1][0] != sec:
            self._volume_buckets.append([float(sec), 0.0, 0.0])
        self._volume_buckets[-1][2 if is_buyer_maker else 1] += float(qty)
        # One aggressive order can print several trades in the same millisecond;
        # count it once so the Hawkes clock sees market orders, not fills.
        key = (ts, is_buyer_maker)
        if key == self._last_order_key:
            return
        self._last_order_key = key
        t = ts / 1000.0
        (self.hawkes_sell if is_buyer_maker else self.hawkes_buy).update(t)
        if self._events is not None:
            self._events[is_buyer_maker].append(t)

    def intensities(self, ts: int) -> tuple[float, float, float]:
        """(buy lambda, sell lambda, ratio to stationary mean)."""
        t = ts / 1000.0
        lb = self.hawkes_buy.evaluate(t)
        ls = self.hawkes_sell.evaluate(t)
        stationary = self.hawkes_buy.stationary_intensity + self.hawkes_sell.stationary_intensity
        return lb, ls, (lb + ls) / stationary

    def regime(self, ts: int) -> str:
        return regime(self.intensities(ts)[2])

    def volume_signals(self, ts: int) -> tuple[float, float]:
        """(signed recent volume imbalance, VPIN-lite bucket toxicity)."""
        if not self._volume_buckets:
            return 0.0, 0.0
        cutoff = ts / 1000.0 - self.volume_signal_s
        recent = [b for b in self._volume_buckets if b[0] >= cutoff]
        buy = sum(b[1] for b in recent)
        sell = sum(b[2] for b in recent)
        imbalance = (buy - sell) / (buy + sell) if buy + sell > 0 else 0.0
        bucket_imbalances = [abs(b[1] - b[2]) / (b[1] + b[2])
                             for b in self._volume_buckets if b[1] + b[2] > 0]
        toxicity = float(np.mean(bucket_imbalances)) if bucket_imbalances else 0.0
        return float(imbalance), toxicity

    def state(self, ts: int | None = None) -> MarketState:
        ts = self.last_ts if ts is None else ts
        lb, ls, ratio = self.intensities(ts)
        total = lb + ls
        volume_imbalance, toxicity = self.volume_signals(ts)
        return MarketState(
            timestamp=ts,
            best_bid=self.bid,
            best_ask=self.ask,
            best_bid_qty=self.bid_qty,
            best_ask_qty=self.ask_qty,
            tick_size=self.tick_size,
            sigma=self.vol.sigma,
            hawkes_ratio=ratio,
            flow_imbalance=(lb - ls) / total if total > 0 else 0.0,
            trade_rate=total,
            signed_volume_imbalance=volume_imbalance,
            volume_toxicity=toxicity,
        )
