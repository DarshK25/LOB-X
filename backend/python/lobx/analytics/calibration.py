"""
analytics/calibration.py — Market data calibration for strategy parameters.

Reference
---------
Avellaneda & Stoikov (2008) "High-frequency trading in a limit order book"
Quantitative Finance.

This module provides functions to calibrate A-S parameters (gamma, sigma, kappa)
from historical or collected market data (Parquet files).
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import pandas as pd
import numpy as np
from scipy import stats


CALIBRATION_ARTIFACT_SCHEMA = "lobx.calibration"
CALIBRATION_ARTIFACT_VERSION = 1
DEFAULT_CALIBRATION_UNITS = {
    "sigma": "log_return_per_sqrt_second",
    "kappa": "1/USD",
    "distance": "USD",
    "timestamp": "milliseconds_since_epoch",
}
MIN_KAPPA_BUCKETS = 8
MIN_KAPPA_R_SQUARED = 0.50
MAX_KAPPA_P_VALUE = 0.05
MAX_KAPPA_BOOTSTRAP_RATIO = 5.0


class CalibrationArtifactError(ValueError):
    """Raised when a calibration artifact is malformed or cannot be used."""


def sha256_file(path: str | Path) -> str:
    """Return the SHA-256 digest for one calibration source file."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class CalibrationArtifact:
    """Versioned, provenance-bearing calibration result.

    The top-level scalar fields intentionally mirror the values consumed by
    the strategy runners.  ``units`` and ``source_hashes`` make those values
    auditable instead of silently coupling a JSON file to a particular
    calibration script.
    """

    schema_version: int = CALIBRATION_ARTIFACT_VERSION
    created_at: str = ""
    symbol: str = "BTCUSDT"
    source_hashes: dict[str, str] = field(default_factory=dict)
    source_files: dict[str, str] = field(default_factory=dict)
    units: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_CALIBRATION_UNITS))
    bucket_count: int = 0
    r_squared: float | None = None
    p_value: float | None = None
    bootstrap_ci_lower: float | None = None
    bootstrap_ci_upper: float | None = None
    bootstrap_reps: int = 0
    bootstrap_block_seconds: int = 60
    valid: bool = False
    usable: bool = False
    sigma: float = 0.0
    kappa: float = 0.0
    validity_reason: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def schema(self) -> str:
        return CALIBRATION_ARTIFACT_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        """Serialize to the stable on-disk representation."""
        return {
            "schema": self.schema,
            "schema_version": self.schema_version,
            "created_at": self.created_at,
            "symbol": self.symbol,
            "source_hashes": dict(self.source_hashes),
            "source_files": dict(self.source_files),
            "units": dict(self.units),
            "bucket_count": self.bucket_count,
            "r_squared": self.r_squared,
            "p_value": self.p_value,
            "bootstrap_ci_lower": self.bootstrap_ci_lower,
            "bootstrap_ci_upper": self.bootstrap_ci_upper,
            "bootstrap_reps": self.bootstrap_reps,
            "bootstrap_block_seconds": self.bootstrap_block_seconds,
            "valid": self.valid,
            "usable": self.usable,
            "validity": {
                "valid": self.valid,
                "usable": self.usable,
                "reason": self.validity_reason,
            },
            "parameters": {
                "sigma": self.sigma,
                "kappa": self.kappa,
            },
            # Keep the calibrated values at the top level as well.  This is
            # convenient for simple consumers and is part of schema v1.
            "sigma": self.sigma,
            "kappa": self.kappa,
            "metadata": dict(self.metadata),
        }

    def validate(self, *, require_usable: bool = False) -> "CalibrationArtifact":
        """Validate schema and strategy-safety invariants."""
        if self.schema_version != CALIBRATION_ARTIFACT_VERSION:
            raise CalibrationArtifactError(
                f"unsupported calibration artifact version: {self.schema_version}"
            )
        if not self.symbol:
            raise CalibrationArtifactError("calibration artifact symbol is required")
        missing_units = {"sigma", "kappa", "timestamp"} - set(self.units)
        if missing_units:
            raise CalibrationArtifactError(
                f"calibration artifact missing units: {sorted(missing_units)}"
            )
        if self.bucket_count < 0:
            raise CalibrationArtifactError("bucket_count cannot be negative")
        if not self.source_hashes:
            raise CalibrationArtifactError("calibration artifact must include source hashes")
        for name, digest in self.source_hashes.items():
            if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest.lower()):
                raise CalibrationArtifactError(f"invalid SHA-256 source hash for {name!r}")
        for name, value in (("sigma", self.sigma), ("kappa", self.kappa)):
            if not math.isfinite(value):
                raise CalibrationArtifactError(f"calibrated {name} must be finite")
        if self.r_squared is not None and not 0.0 <= self.r_squared <= 1.0:
            raise CalibrationArtifactError("r_squared must be between 0 and 1")
        if self.p_value is not None and not 0.0 <= self.p_value <= 1.0:
            raise CalibrationArtifactError("p_value must be between 0 and 1")
        for name, value in (("bootstrap_ci_lower", self.bootstrap_ci_lower), ("bootstrap_ci_upper", self.bootstrap_ci_upper)):
            if value is not None and (not math.isfinite(value) or value < 0.0):
                raise CalibrationArtifactError(f"{name} must be finite and non-negative")
        if self.usable and not self.valid:
            raise CalibrationArtifactError("a usable calibration artifact must be valid")
        if self.usable and (self.sigma <= 0.0 or self.kappa <= 0.0):
            raise CalibrationArtifactError("a usable artifact needs positive sigma and kappa")
        if self.usable and self.bucket_count < MIN_KAPPA_BUCKETS:
            raise CalibrationArtifactError(
                f"a usable artifact needs at least {MIN_KAPPA_BUCKETS} calibration buckets"
            )
        if self.usable and (self.r_squared is None or self.p_value is None):
            raise CalibrationArtifactError("a usable artifact needs R-squared and p-value")
        if self.usable and self.r_squared is not None and self.r_squared < MIN_KAPPA_R_SQUARED:
            raise CalibrationArtifactError("a usable artifact needs R-squared >= 0.50")
        if self.usable and (self.p_value is None or self.p_value >= MAX_KAPPA_P_VALUE):
            raise CalibrationArtifactError("a usable artifact needs p-value < 0.05")
        if self.usable and (
            self.bootstrap_ci_lower is None
            or self.bootstrap_ci_upper is None
            or self.bootstrap_ci_lower <= 0.0
            or self.bootstrap_ci_upper / self.bootstrap_ci_lower > MAX_KAPPA_BOOTSTRAP_RATIO
        ):
            raise CalibrationArtifactError(
                "a usable artifact needs a positive 95% time-block bootstrap CI "
                "with upper/lower <= 5"
            )
        if require_usable and not self.usable:
            reason = f": {self.validity_reason}" if self.validity_reason else ""
            raise CalibrationArtifactError(f"calibration artifact is not usable{reason}")
        return self

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "CalibrationArtifact":
        """Construct and validate one artifact from decoded JSON."""
        if payload.get("schema") != CALIBRATION_ARTIFACT_SCHEMA:
            raise CalibrationArtifactError(
                f"unexpected calibration artifact schema: {payload.get('schema')!r}"
            )
        validity = payload.get("validity") or {}
        parameters = payload.get("parameters") or {}
        artifact = cls(
            schema_version=int(payload.get("schema_version", -1)),
            created_at=str(payload.get("created_at", "")),
            symbol=str(payload.get("symbol", "")),
            source_hashes={str(k): str(v) for k, v in (payload.get("source_hashes") or {}).items()},
            source_files={str(k): str(v) for k, v in (payload.get("source_files") or {}).items()},
            units={str(k): str(v) for k, v in (payload.get("units") or {}).items()},
            bucket_count=int(payload.get("bucket_count", 0)),
            r_squared=_optional_float(payload.get("r_squared")),
            p_value=_optional_float(payload.get("p_value")),
            bootstrap_ci_lower=_optional_float(payload.get("bootstrap_ci_lower")),
            bootstrap_ci_upper=_optional_float(payload.get("bootstrap_ci_upper")),
            bootstrap_reps=int(payload.get("bootstrap_reps", 0)),
            bootstrap_block_seconds=int(payload.get("bootstrap_block_seconds", 60)),
            valid=bool(payload.get("valid", validity.get("valid", False))),
            usable=bool(payload.get("usable", validity.get("usable", False))),
            sigma=float(payload.get("sigma", parameters.get("sigma", 0.0))),
            kappa=float(payload.get("kappa", parameters.get("kappa", 0.0))),
            validity_reason=payload.get("validity_reason", validity.get("reason")),
            metadata=dict(payload.get("metadata") or {}),
        )
        return artifact.validate()


def _optional_float(value: Any) -> float | None:
    return None if value is None else float(value)


def _source_provenance(
    source_files: Mapping[str, str | Path] | None,
    source_hashes: Mapping[str, str] | None,
) -> tuple[dict[str, str], dict[str, str]]:
    if source_files and source_hashes:
        raise CalibrationArtifactError("provide source_files or source_hashes, not both")
    files = {str(name): str(path) for name, path in (source_files or {}).items()}
    hashes = (
        {str(name): str(digest).lower() for name, digest in source_hashes.items()}
        if source_hashes is not None
        else {name: sha256_file(path) for name, path in files.items()}
    )
    return files, hashes


def write_calibration_artifact(
    path: str | Path,
    *,
    sigma: float,
    kappa: float,
    source_files: Mapping[str, str | Path] | None = None,
    source_hashes: Mapping[str, str] | None = None,
    units: Mapping[str, str] | None = None,
    bucket_count: int = 0,
    r_squared: float | None = None,
    p_value: float | None = None,
    bootstrap_ci_lower: float | None = None,
    bootstrap_ci_upper: float | None = None,
    bootstrap_reps: int = 0,
    bootstrap_block_seconds: int = 60,
    valid: bool | None = None,
    usable: bool | None = None,
    validity_reason: str | None = None,
    symbol: str = "BTCUSDT",
    metadata: Mapping[str, Any] | None = None,
) -> CalibrationArtifact:
    """Write a v1 calibration artifact and return its validated object.

    ``source_files`` is preferred for production use because the writer hashes
    the exact bytes used for calibration.  ``source_hashes`` is available for
    pipelines that already calculated and recorded those digests.
    """
    files, hashes = _source_provenance(source_files, source_hashes)
    finite_fit = (
        math.isfinite(float(sigma))
        and math.isfinite(float(kappa))
        and float(sigma) > 0.0
        and float(kappa) > 0.0
    )
    fit_quality_ok = (
        r_squared is not None
        and p_value is not None
        and r_squared >= MIN_KAPPA_R_SQUARED
        and p_value < MAX_KAPPA_P_VALUE
        and int(bucket_count) >= MIN_KAPPA_BUCKETS
        and bootstrap_ci_lower is not None
        and bootstrap_ci_upper is not None
        and bootstrap_ci_lower > 0.0
        and bootstrap_ci_upper / bootstrap_ci_lower <= MAX_KAPPA_BOOTSTRAP_RATIO
    )
    artifact_valid = finite_fit if valid is None else bool(valid)
    artifact_usable = (
        artifact_valid and finite_fit and fit_quality_ok and bool(hashes)
        if usable is None
        else bool(usable)
    )
    artifact_metadata = dict(metadata or {})
    try:
        from lobx.analytics.provenance import git_provenance, parameter_hash
        artifact_metadata.setdefault("provenance", git_provenance())
        artifact_metadata.setdefault(
            "parameter_hash",
            parameter_hash({"sigma": sigma, "kappa": kappa, "bucket_count": bucket_count}),
        )
    except Exception:
        # Artifact creation remains usable outside a git checkout.
        pass
    artifact = CalibrationArtifact(
        created_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        symbol=symbol,
        source_hashes=hashes,
        source_files=files,
        units={**DEFAULT_CALIBRATION_UNITS, **(units or {})},
        bucket_count=int(bucket_count),
        r_squared=None if r_squared is None else float(r_squared),
            p_value=None if p_value is None else float(p_value),
            bootstrap_ci_lower=None if bootstrap_ci_lower is None else float(bootstrap_ci_lower),
            bootstrap_ci_upper=None if bootstrap_ci_upper is None else float(bootstrap_ci_upper),
            bootstrap_reps=int(bootstrap_reps),
            bootstrap_block_seconds=int(bootstrap_block_seconds),
        valid=artifact_valid,
        usable=artifact_usable,
        sigma=float(sigma),
        kappa=float(kappa),
        validity_reason=validity_reason,
        metadata=artifact_metadata,
    ).validate()
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(artifact.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return artifact


def load_calibration_artifact(
    path: str | Path,
    *,
    require_usable: bool = False,
) -> CalibrationArtifact:
    """Load and validate a versioned calibration artifact from JSON."""
    source = Path(path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CalibrationArtifactError(f"calibration artifact not found: {source}") from exc
    except json.JSONDecodeError as exc:
        raise CalibrationArtifactError(f"invalid calibration artifact JSON: {source}") from exc
    if not isinstance(payload, dict):
        raise CalibrationArtifactError("calibration artifact JSON must contain an object")
    return CalibrationArtifact.from_dict(payload).validate(require_usable=require_usable)


def calibrate_sigma(depth_df: pd.DataFrame, time_col: str = 'event_time', price_col: str = 'price') -> float:
    """Calibrate log-volatility per sqrt(second) from a price DataFrame.

    The returned unit is the same log-return per sqrt(second) consumed by the
    strategy's calibration floor and rolling realized-volatility estimator.
    Conversion to USD per sqrt(second) is deliberately performed only at the
    AS model boundary.
    """
    if len(depth_df) < 2:
        return 0.0
        
    prices = depth_df[price_col].to_numpy(dtype=float)

    if np.any(prices <= 0.0) or not np.all(np.isfinite(prices)):
        raise ValueError("calibration prices must be finite and positive")

    log_returns = np.diff(np.log(prices))
    times = depth_df[time_col].to_numpy(dtype=float)
    dt_sec = np.diff(times) / 1000.0
    # Equal timestamps carry no elapsed-time information.  Keep the existing
    # defensive behavior while expressing the result in per-sqrt-second units.
    dt_sec = np.maximum(dt_sec, 0.001)

    scaled_returns = log_returns / np.sqrt(dt_sec)
    return float(np.std(scaled_returns))


def gamma_for_target_skew(
    target_ticks: float,
    inventory_limit: float,
    sigma_usd: float,
    tau: float,
    tick: float,
) -> float:
    """Solve AS gamma for a target reservation-price skew at the limit.

    At ``inventory_limit`` the AS reservation-price shift is
    ``q * gamma * sigma_usd**2 * tau``.  This helper sets that shift equal to
    ``target_ticks * tick`` and returns the positive gamma implied by the
    requested design target.
    """
    from lobx.models.avellaneda.model import gamma_for_target_skew as _gamma_for_target_skew

    return _gamma_for_target_skew(
        target_ticks=target_ticks,
        inventory_limit=inventory_limit,
        sigma_usd=sigma_usd,
        tau=tau,
        tick=tick,
    )


def calibrate_kappa(trades_df: pd.DataFrame, time_col: str = 'event_time') -> float:
    if len(trades_df) < 2:
        return 1.5
        
    actual_col = time_col if time_col in trades_df.columns else ('transact_time' if 'transact_time' in trades_df.columns else trades_df.columns[0])
    times = trades_df[actual_col].values
    total_time_sec = (times[-1] - times[0]) / 1000.0
    
    if total_time_sec <= 0:
        return 1.5
        
    # Number of market orders
    num_trades = len(trades_df)
    
    # Lambda is arrival rate (trades per second)
    arrival_rate = num_trades / total_time_sec
    
    # A very rough approximation for kappa based on arrival intensity.
    # In a full estimation, we would regress log(lambda(delta)) against delta.
    # Here we use a heuristic related to the arrival rate.
    # Higher arrival rate -> lower kappa (fills happen further in the book).
    
    kappa = 1.5 / max(math.log10(arrival_rate + 1.1), 0.1)
    # Bound kappa to sensible limits for crypto (0.1 to 10.0)
    return float(np.clip(kappa, 0.1, 10.0))
    

def calibrate_gamma(sigma: float, max_inventory: float, risk_tolerance: float = 0.5) -> float:
    """Suggest a gamma (risk aversion) parameter.
    
    Gamma controls how strongly the strategy reacts to inventory.
    Higher gamma = more risk averse = larger reservation price shifts.
    
    Risk tolerance is in [0, 1] where 1 is highly risk seeking.
    """
    # A heuristic mapping: if you hold max_inventory, how much of sigma 
    # are you willing to risk?
    # shift = inventory * gamma * sigma^2
    # If we want max_shift to be roughly equal to sigma at max_inventory:
    # sigma = max_inv * gamma * sigma^2 => gamma = 1 / (max_inv * sigma)
    
    if sigma <= 0 or max_inventory <= 0:
        return 0.1
        
    baseline_gamma = 1.0 / (max_inventory * sigma)
    
    # Modulate by risk tolerance
    # risk_tol = 0 -> 2x baseline (more risk averse)
    # risk_tol = 1 -> 0.5x baseline (less risk averse)
    modifier = 2.0 - 1.5 * risk_tolerance
    
    return float(baseline_gamma * modifier)


def empirical_fill_curve(
    depth_df: pd.DataFrame,
    trades_df: pd.DataFrame,
    sample_interval_ms: int = 1_000,
    levels_per_side: int = 10,
) -> tuple[pd.DataFrame, float]:
    """Estimate observed passive-quote fill intensity from collected data.

    At *sample_interval_ms* intervals the best-bid/best-ask mid-price is
    derived from the depth data and used to assign exposure counts per
    distance bucket.  Trades are matched to the contemporaneous mid-price
    and counted in distance buckets.  The result is an observed fill *rate*
    (fills / quote-second), the empirical quantity used by the A-S
    ``lambda(delta)`` model.

    This implementation is fully vectorised: depth is resampled to a
    regular grid using pandas operations rather than row-by-row Python
    iteration, making it tractable on multi-million-row overnight files.
    """
    required_depth = {"event_time", "side", "price", "qty"}
    required_trades = {"event_time", "price", "is_buyer_maker"}
    if not required_depth.issubset(depth_df.columns):
        raise ValueError(f"depth data missing {required_depth - set(depth_df.columns)}")
    if not required_trades.issubset(trades_df.columns):
        raise ValueError(f"trade data missing {required_trades - set(trades_df.columns)}")

    depth = depth_df.loc[:, list(required_depth)].copy()
    depth["price"] = depth["price"].astype(float)
    depth["qty"] = depth["qty"].astype(float)
    trades = trades_df.loc[:, ["event_time", "price", "is_buyer_maker"]].copy()
    trades["price"] = trades["price"].astype(float)
    if depth.empty or trades.empty:
        raise ValueError("depth and trade data must both contain rows")

    # ── Infer tick size ───────────────────────────────────────────────────────
    unique_prices = np.unique(depth["price"].to_numpy())
    steps = np.diff(unique_prices)
    steps = steps[steps > 1e-9]
    if len(steps) == 0:
        raise ValueError("unable to infer a price tick from depth data")
    tick_size = float(np.round(np.min(steps), 10))

    # ── Vectorised best-bid / best-ask time series ────────────────────────────
    # Bin depth updates into sample_interval_ms windows. Within each bin, the
    # last qty update per (side, price) wins. Then best bid = max active price
    # on bid side, best ask = min active price on ask side, per bin.
    bin_ms = sample_interval_ms
    depth = depth.sort_values("event_time", kind="stable")
    depth["bin"] = (depth["event_time"].astype(np.int64) // bin_ms) * bin_ms

    last_update = (
        depth.groupby(["bin", "side", "price"], sort=False)["qty"]
        .last()
        .reset_index()
    )

    bids_df = last_update[(last_update["side"] == "bid") & (last_update["qty"] > 0)]
    asks_df = last_update[(last_update["side"] == "ask") & (last_update["qty"] > 0)]

    best_bid_series = bids_df.groupby("bin")["price"].max() if not bids_df.empty else pd.Series(dtype=float)
    best_ask_series = asks_df.groupby("bin")["price"].min() if not asks_df.empty else pd.Series(dtype=float)

    # Forward-fill across all bins so gaps in the depth stream are bridged
    all_bins = np.arange(int(depth["bin"].min()), int(depth["bin"].max()) + bin_ms, bin_ms)
    all_bins_idx = pd.Index(all_bins)
    best_bid = best_bid_series.reindex(all_bins_idx).ffill()
    best_ask = best_ask_series.reindex(all_bins_idx).ffill()

    valid = (best_bid.notna()) & (best_ask.notna()) & (best_bid < best_ask)
    mid_series = ((best_bid + best_ask) / 2.0)[valid]

    if mid_series.empty:
        raise ValueError("insufficient overlapping reconstructed depth snapshots")

    # ── Exposure: uniform across all sub-tick distance bins ─────────────────
    # We use 0.001 USD steps (10× finer than the 0.01 exchange tick) so that
    # fills clustered near the best bid/ask are spread across multiple bins
    # rather than all collapsing into a single "1 tick" bucket.
    SUB_TICK_STEP = 0.001  # USD per bucket
    # Maximum distance to track: levels_per_side exchange ticks away from mid
    max_distance_usd = levels_per_side * tick_size
    n_samples = int(len(mid_series))
    # Build exposure dict keyed by bucket centre (rounded to SUB_TICK_STEP)
    bucket_edges = np.round(
        np.arange(SUB_TICK_STEP, max_distance_usd + SUB_TICK_STEP, SUB_TICK_STEP),
        6,
    )
    exposures: dict[float, int] = {float(b): n_samples * 2 for b in bucket_edges}

    # ── Match trades to nearest preceding mid-price via merge_asof ────────────
    mid_df = mid_series.rename("mid").reset_index()
    mid_df.columns = ["event_time", "mid"]
    mid_df["event_time"] = mid_df["event_time"].astype(np.int64)
    trades_sorted = trades.sort_values("event_time").reset_index(drop=True)
    trades_sorted["event_time"] = trades_sorted["event_time"].astype(np.int64)

    matched = pd.merge_asof(
        trades_sorted,
        mid_df,
        on="event_time",
        direction="backward",
    ).dropna(subset=["mid"])

    # Compute raw USD distance from mid, then snap to nearest SUB_TICK_STEP
    matched["distance_raw"] = (matched["price"] - matched["mid"]).abs()
    matched["distance_usd"] = (
        (matched["distance_raw"] / SUB_TICK_STEP).round() * SUB_TICK_STEP
    ).round(6)
    # Clamp: distance 0 → first bucket; distance > max → excluded
    matched["distance_usd"] = matched["distance_usd"].clip(lower=SUB_TICK_STEP)
    matched = matched[matched["distance_usd"] <= max_distance_usd]
    fills: dict[float, int] = matched.groupby("distance_usd").size().to_dict()

    # ── Assemble output ───────────────────────────────────────────────────────
    rows = []
    for bucket, quote_observations in sorted(exposures.items()):
        count = int(fills.get(bucket, 0))
        rows.append({
            "distance_usd": bucket,
            "fill_count": count,
            "quote_observations": quote_observations,
            "exposure_quote_seconds": quote_observations * bin_ms / 1000.0,
            "fill_rate_per_quote_second": count / (quote_observations * bin_ms / 1000.0),
        })
    return pd.DataFrame(rows), tick_size


def fit_kappa_from_fill_curve(curve: pd.DataFrame, min_fills: int = 20, min_r_squared: float = 0.50) -> dict:
    """Fit ``log(lambda(delta)) = intercept - kappa * delta`` to real data.

    The curve DataFrame must have columns ``distance_usd``,
    ``fill_count``, and ``fill_rate_per_quote_second``.
    kappa is returned in units of 1/USD (distance in USD).
    """
    fit = curve.loc[
        (curve["fill_count"] >= min_fills)
        & (curve["fill_rate_per_quote_second"] > 0),
        ["distance_usd", "fill_rate_per_quote_second"],
    ]
    if len(fit) < MIN_KAPPA_BUCKETS:
        raise ValueError(
            "not enough populated distance buckets for kappa fit; "
            f"strict R-03 gate needs at least {MIN_KAPPA_BUCKETS}, found {len(fit)}"
        )
    result = stats.linregress(
        fit["distance_usd"].to_numpy(),
        np.log(fit["fill_rate_per_quote_second"].to_numpy()),
    )
    r_squared = float(result.rvalue ** 2)
    if result.slope >= 0:
        raise ValueError(
            "empirical fill rate does not decay with quote distance "
            f"(slope={result.slope:.8f}); kappa would be non-positive. "
            "Collect a fresh, complete depth/trade session before using this calibration."
        )
    if r_squared < min_r_squared:
        raise ValueError(
            f"kappa fit quality is too poor (R² = {r_squared:.3f} < {min_r_squared}). "
            "The distance buckets do not show a clear exponential decay pattern. "
            "Collect a larger dataset or pool sessions."
        )
    if result.pvalue >= MAX_KAPPA_P_VALUE:
        raise ValueError(
            f"kappa fit p-value is too high (p = {result.pvalue:.6g} >= {MAX_KAPPA_P_VALUE}); "
            "the fit is not statistically usable"
        )
    return {
        "kappa": float(-result.slope),
        "intercept": float(result.intercept),
        "r_squared": r_squared,
        "p_value": float(result.pvalue),
        "std_err": float(result.stderr),
        "n_points": int(len(fit)),
        "fit_curve": fit.reset_index(drop=True),
        "bootstrap_ci_lower": None,
        "bootstrap_ci_upper": None,
        "bootstrap_reps": 0,
        "bootstrap_block_seconds": 60,
        "usable": False,
    }
