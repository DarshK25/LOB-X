"""Analytics sub-package."""
# Core modules that require lobx_cpp - import on demand only

def get_pnl_tracker():
    """Lazy import PnLTracker (requires lobx_cpp extension)."""
    from lobx.analytics.pnl import PnLTracker
    return PnLTracker

def get_vwap_calculator():
    """Lazy import VWAPCalculator (requires lobx_cpp extension)."""
    from lobx.analytics.vwap import VWAPCalculator
    return VWAPCalculator

def get_spread_analyzer():
    """Lazy import SpreadAnalyzer (requires lobx_cpp extension)."""
    from lobx.analytics.spread import SpreadAnalyzer
    return SpreadAnalyzer

from lobx.analytics.regimes import (
    RegimeConfig,
    attribute_fills_to_regimes,
    sample_market_states,
    summarise_regimes,
)

__all__ = [
    "RegimeConfig",
    "sample_market_states",
    "attribute_fills_to_regimes",
    "summarise_regimes",
    "get_pnl_tracker",
    "get_vwap_calculator",
    "get_spread_analyzer",
]
