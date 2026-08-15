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

import math
import pandas as pd
import numpy as np
from scipy import stats


def calibrate_sigma(depth_df: pd.DataFrame, time_col: str = 'event_time', price_col: str = 'price') -> float:
    """Calibrate volatility (sigma) from a depth DataFrame.
    
    Assumes standard Brownian motion scaling.
    Sigma represents the absolute price volatility (standard deviation of price changes).
    """
    if len(depth_df) < 2:
        return 0.0
        
    # Take mid price if possible, or just use the price series given
    prices = depth_df[price_col].values
    
    # Calculate price differences (dt)
    dp = np.diff(prices)
    
    # Calculate time differences in seconds
    times = depth_df[time_col].values
    dt_ms = np.diff(times)
    # Avoid div by zero if timestamps are identical
    dt_sec = np.maximum(dt_ms / 1000.0, 0.001)
    
    # Sigma is the std of (dp / sqrt(dt))
    scaled_dp = dp / np.sqrt(dt_sec)
    
    # We want sigma in price units per sqrt(second)
    sigma = np.std(scaled_dp)
    return float(sigma)


def calibrate_kappa(trades_df: pd.DataFrame, time_col: str = 'event_time') -> float:
    """Calibrate fill probability decay (kappa) from trades DataFrame.
    
    In Avellaneda-Stoikov, lambda(delta) = A * exp(-kappa * delta).
    This function estimates kappa by analyzing the empirical distribution 
    of trade arrivals.
    
    A simplified estimation based on average trade frequency and spread.
    """
    if len(trades_df) < 2:
        return 1.5 # fallback default
        
    times = trades_df[time_col].values
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
    if len(fit) < 2:
        raise ValueError(
            "not enough populated distance buckets for kappa fit; "
            f"need at least 2, found {len(fit)}"
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
    return {
        "kappa": float(-result.slope),
        "intercept": float(result.intercept),
        "r_squared": r_squared,
        "p_value": float(result.pvalue),
        "std_err": float(result.stderr),
        "n_points": int(len(fit)),
        "fit_curve": fit.reset_index(drop=True),
        "usable": True,
    }
