"""Hawkes-process risk gating for market-making quotes.

The controller deliberately owns only event intensity and quote treatment.  It
does not replace the hard position, notional, stale-data, or drawdown checks in
``lobx.risk``.  Timestamps accepted by this module are exchange milliseconds,
which is the timestamp convention used by the replay and live paths.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
from math import isfinite
from pathlib import Path
from typing import Mapping

from lobx.models.hawkes.intensity import HawkesIntensity
from lobx.strategy.base import Quote, QuotePair


class HawkesRegime(str, Enum):
    """Quote-risk regimes derived from intensity relative to ``mu``."""

    QUIET = "QUIET"
    ELEVATED = "ELEVATED"
    CASCADE = "CASCADE"


@dataclass(frozen=True)
class HawkesParameters:
    """Calibrated exponential-Hawkes parameters.

    The object can be built directly from the dictionary returned by the
    existing calibration routine with :meth:`from_mapping`; calibration itself
    remains outside the risk boundary.
    """

    mu: float
    alpha: float
    beta: float

    def __post_init__(self) -> None:
        if not all(isfinite(value) and value > 0 for value in (self.mu, self.alpha, self.beta)):
            raise ValueError("Hawkes parameters must be finite and positive")
        if self.alpha >= self.beta:
            raise ValueError("Hawkes parameters must satisfy alpha < beta")

    @classmethod
    def from_mapping(cls, values: Mapping[str, float]) -> "HawkesParameters":
        """Load ``mu``, ``alpha`` and ``beta`` from calibrated output."""
        try:
            return cls(
                mu=float(values["mu"]),
                alpha=float(values["alpha"]),
                beta=float(values["beta"]),
            )
        except KeyError as exc:
            raise ValueError("calibrated Hawkes parameters require mu, alpha, and beta") from exc


@dataclass(frozen=True)
class HawkesRiskConfig:
    """Policy for converting Hawkes intensity into quote treatment."""

    parameters: HawkesParameters = field(
        default_factory=lambda: HawkesParameters(mu=1.0, alpha=0.8, beta=2.0)
    )
    elevated_ratio: float = 2.0
    cascade_ratio: float = 5.0
    elevated_spread_multiplier: float = 1.5
    cooldown_ms: int = 1_000
    pull_quotes_on_cascade: bool = True
    max_trade_ids: int = 50_000

    def __post_init__(self) -> None:
        if self.elevated_ratio <= 1.0:
            raise ValueError("elevated_ratio must be greater than 1")
        if self.cascade_ratio <= self.elevated_ratio:
            raise ValueError("cascade_ratio must exceed elevated_ratio")
        if self.elevated_spread_multiplier < 1.0:
            raise ValueError("elevated_spread_multiplier must be at least 1")
        if self.cooldown_ms < 0:
            raise ValueError("cooldown_ms cannot be negative")
        if self.max_trade_ids <= 0:
            raise ValueError("max_trade_ids must be positive")

    # Short aliases make the policy readable at call sites while retaining a
    # descriptive canonical field for serialized configuration.
    @property
    def spread_multiplier(self) -> float:
        return self.elevated_spread_multiplier

    @property
    def quote_pull(self) -> bool:
        return self.pull_quotes_on_cascade


@dataclass(frozen=True)
class HawkesRiskDecision:
    """Auditable controller output at one market timestamp."""

    timestamp_ms: int
    intensity: float
    ratio: float
    regime: HawkesRegime
    cooldown_until_ms: int | None
    cooldown_active: bool
    spread_multiplier: float
    quotes_pulled: bool


class HawkesRiskController:
    """Track trade arrivals and turn them into quote-risk decisions.

    ``on_trade`` is duplicate-safe by exchange trade ID.  A duplicate may
    still advance the passive decay evaluation, but it can never add another
    Hawkes excitation jump.  Out-of-order unique trades are retained for
    identity protection and do not rewind the process state.
    """

    def __init__(
        self,
        parameters: HawkesParameters | Mapping[str, float] | None = None,
        *,
        config: HawkesRiskConfig | None = None,
        mu: float | None = None,
        alpha: float | None = None,
        beta: float | None = None,
        elevated_spread_multiplier: float | None = None,
        cooldown_ms: int | None = None,
        pull_quotes_on_cascade: bool | None = None,
    ) -> None:
        if parameters is not None and config is not None:
            raise ValueError("pass either parameters or config, not both")
        if any(value is not None for value in (mu, alpha, beta)):
            if not all(value is not None for value in (mu, alpha, beta)):
                raise ValueError("mu, alpha, and beta must be provided together")
            if parameters is not None:
                raise ValueError("individual parameters cannot be combined with parameters")
            parameters = HawkesParameters(float(mu), float(alpha), float(beta))
        elif isinstance(parameters, Mapping):
            parameters = HawkesParameters.from_mapping(parameters)

        if config is None:
            config = HawkesRiskConfig(parameters=parameters or HawkesRiskConfig().parameters)
        elif parameters is not None:
            raise ValueError("pass either parameters or config, not both")

        if elevated_spread_multiplier is not None or cooldown_ms is not None or pull_quotes_on_cascade is not None:
            config = HawkesRiskConfig(
                parameters=config.parameters,
                elevated_ratio=config.elevated_ratio,
                cascade_ratio=config.cascade_ratio,
                elevated_spread_multiplier=(
                    config.elevated_spread_multiplier
                    if elevated_spread_multiplier is None
                    else elevated_spread_multiplier
                ),
                cooldown_ms=config.cooldown_ms if cooldown_ms is None else cooldown_ms,
                pull_quotes_on_cascade=(
                    config.pull_quotes_on_cascade
                    if pull_quotes_on_cascade is None
                    else pull_quotes_on_cascade
                ),
                max_trade_ids=config.max_trade_ids,
            )

        self.config = config
        self.parameters = config.parameters
        self._process = HawkesIntensity(
            mu=self.parameters.mu,
            alpha=self.parameters.alpha,
            beta=self.parameters.beta,
        )
        self._seen_trade_ids: set[object] = set()
        self._trade_id_order: deque[object] = deque()
        self._evaluation_ms = 0
        self._last_event_ms: int | None = None
        self._last_intensity = self.parameters.mu
        self._regime = HawkesRegime.QUIET
        self._cooldown_until_ms: int | None = None
        self.duplicate_trade_count = 0
        self.accepted_trade_count = 0

    @classmethod
    def from_calibrated(
        cls,
        calibrated: Mapping[str, float],
        **kwargs: object,
    ) -> "HawkesRiskController":
        """Construct a controller from existing calibration output."""
        return cls(parameters=HawkesParameters.from_mapping(calibrated), **kwargs)

    @property
    def intensity(self) -> float:
        return self._last_intensity

    @property
    def ratio(self) -> float:
        return self._last_intensity / self.parameters.mu

    @property
    def regime(self) -> HawkesRegime:
        return self._regime

    @property
    def cooldown_until_ms(self) -> int | None:
        return self._cooldown_until_ms

    @property
    def seen_trade_ids(self) -> int:
        """Number of IDs retained by the duplicate guard."""
        return len(self._seen_trade_ids)

    def reset(self) -> None:
        """Reset process, regime, cooldown, and duplicate identity state."""
        self._process = HawkesIntensity(
            mu=self.parameters.mu,
            alpha=self.parameters.alpha,
            beta=self.parameters.beta,
        )
        self._seen_trade_ids.clear()
        self._trade_id_order.clear()
        self._evaluation_ms = 0
        self._last_event_ms = None
        self._last_intensity = self.parameters.mu
        self._regime = HawkesRegime.QUIET
        self._cooldown_until_ms = None
        self.duplicate_trade_count = 0
        self.accepted_trade_count = 0

    def on_trade(self, trade_id: object, timestamp_ms: int) -> HawkesRiskDecision:
        """Register one exchange trade and return the resulting decision."""
        timestamp_ms = int(timestamp_ms)
        if trade_id in self._seen_trade_ids:
            self.duplicate_trade_count += 1
            return self.evaluate(timestamp_ms)

        self._seen_trade_ids.add(trade_id)
        self._trade_id_order.append(trade_id)
        while len(self._trade_id_order) > self.config.max_trade_ids:
            expired = self._trade_id_order.popleft()
            self._seen_trade_ids.discard(expired)

        self.accepted_trade_count += 1
        if self._last_event_ms is None or timestamp_ms >= self._last_event_ms:
            self._last_intensity = float(self._process.update(timestamp_ms / 1_000.0))
            self._last_event_ms = timestamp_ms
            self._evaluation_ms = max(self._evaluation_ms, timestamp_ms)
        return self._refresh(timestamp_ms)

    def observe_trade(self, trade_id: object, timestamp_ms: int) -> HawkesRiskDecision:
        """Named alias for callers that prefer event-oriented terminology."""
        return self.on_trade(trade_id, timestamp_ms)

    def evaluate(self, timestamp_ms: int | None = None) -> HawkesRiskDecision:
        """Evaluate decay without registering a new trade."""
        timestamp = self._evaluation_ms if timestamp_ms is None else int(timestamp_ms)
        if timestamp >= self._evaluation_ms:
            self._last_intensity = float(self._process.evaluate(timestamp / 1_000.0))
            self._evaluation_ms = timestamp
        return self._refresh(timestamp)

    def decision(self, timestamp_ms: int | None = None) -> HawkesRiskDecision:
        """Return the current decision, applying passive decay if requested."""
        return self.evaluate(timestamp_ms)

    def adjust_quotes(
        self,
        quotes: QuotePair,
        *,
        market_mid: float | None = None,
        timestamp_ms: int | None = None,
        decision: HawkesRiskDecision | None = None,
    ) -> QuotePair:
        """Pull or widen quotes according to the current Hawkes regime."""
        current = decision or self.evaluate(timestamp_ms)
        if current.quotes_pulled:
            return QuotePair(bid=None, ask=None)
        if current.regime is not HawkesRegime.ELEVATED:
            return quotes

        mid = market_mid
        if mid is None and quotes.bid is not None and quotes.ask is not None:
            mid = (quotes.bid.price + quotes.ask.price) / 2.0
        if mid is None:
            return quotes

        def widen(quote: Quote | None, is_bid: bool) -> Quote | None:
            if quote is None:
                return None
            distance = abs(quote.price - mid)
            price = mid - distance * current.spread_multiplier if is_bid else mid + distance * current.spread_multiplier
            return Quote(price=price, qty=quote.qty)

        return QuotePair(
            bid=widen(quotes.bid, is_bid=True),
            ask=widen(quotes.ask, is_bid=False),
        )

    def _refresh(self, timestamp_ms: int) -> HawkesRiskDecision:
        ratio = self._last_intensity / self.parameters.mu
        if ratio >= self.config.cascade_ratio:
            next_regime = HawkesRegime.CASCADE
        elif ratio >= self.config.elevated_ratio:
            next_regime = HawkesRegime.ELEVATED
        else:
            next_regime = HawkesRegime.QUIET

        if next_regime is HawkesRegime.CASCADE and self._regime is not HawkesRegime.CASCADE:
            self._cooldown_until_ms = timestamp_ms + self.config.cooldown_ms
        self._regime = next_regime
        cooldown_active = (
            self._cooldown_until_ms is not None and timestamp_ms < self._cooldown_until_ms
        )
        pulled = (
            self._regime is HawkesRegime.CASCADE and self.config.pull_quotes_on_cascade
        ) or cooldown_active
        multiplier = (
            self.config.elevated_spread_multiplier
            if self._regime is HawkesRegime.ELEVATED and not pulled
            else 1.0
        )
        return HawkesRiskDecision(
            timestamp_ms=timestamp_ms,
            intensity=self._last_intensity,
            ratio=ratio,
            regime=self._regime,
            cooldown_until_ms=self._cooldown_until_ms,
            cooldown_active=cooldown_active,
            spread_multiplier=multiplier,
            quotes_pulled=pulled,
        )


def load_hawkes_parameters(path: str | Path) -> HawkesParameters:
    """Load validated Hawkes parameters from a JSON calibration artifact."""
    source = Path(path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"Hawkes calibration artifact not found: {source}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid Hawkes calibration artifact JSON: {source}") from exc
    if not isinstance(payload, Mapping):
        raise ValueError("Hawkes calibration artifact must contain a JSON object")
    values = payload.get("parameters", payload)
    if not isinstance(values, Mapping):
        raise ValueError("Hawkes calibration artifact parameters must be an object")
    return HawkesParameters.from_mapping(values)


def write_hawkes_artifact(
    path: str | Path,
    parameters: Mapping[str, float],
    *,
    source_file: str | Path | None = None,
    observation_window_seconds: float | None = None,
    event_count: int | None = None,
    thresholds: Mapping[str, float] | None = None,
) -> Path:
    """Write a provenance-bearing Hawkes calibration artifact."""
    validated = HawkesParameters.from_mapping(parameters)
    source_hash = None
    if source_file is not None:
        digest = hashlib.sha256()
        with Path(source_file).open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        source_hash = digest.hexdigest()
    payload = {
        "schema": "lobx.hawkes_calibration",
        "schema_version": 1,
        "parameters": {
            "mu": validated.mu,
            "alpha": validated.alpha,
            "beta": validated.beta,
        },
        "source_file": str(source_file) if source_file is not None else None,
        "source_sha256": source_hash,
        "observation_window_seconds": observation_window_seconds,
        "event_count": event_count,
        "thresholds": dict(thresholds or {}),
    }
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return output
