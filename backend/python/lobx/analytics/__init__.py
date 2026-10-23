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
from lobx.analytics.calibration_artifact import (
    CALIBRATION_ARTIFACT_SCHEMA,
    CALIBRATION_ARTIFACT_VERSION,
    CalibrationArtifact,
    CalibrationArtifactError,
    load_calibration_artifact,
    write_calibration_artifact,
)
from lobx.analytics.calibration import gamma_for_target_skew
from lobx.analytics.realized_volatility import TimestampAwareRollingRealizedVolatility
from lobx.analytics.bootstrap import bootstrap_mean_ci
from lobx.analytics.pnl_summary import summarize_fills
from lobx.analytics.provenance import git_provenance, parameter_hash

__all__ = [
    "RegimeConfig",
    "sample_market_states",
    "attribute_fills_to_regimes",
    "summarise_regimes",
    "get_pnl_tracker",
    "get_vwap_calculator",
    "get_spread_analyzer",
    "CalibrationArtifact",
    "CalibrationArtifactError",
    "CALIBRATION_ARTIFACT_SCHEMA",
    "CALIBRATION_ARTIFACT_VERSION",
    "load_calibration_artifact",
    "write_calibration_artifact",
    "gamma_for_target_skew",
    "TimestampAwareRollingRealizedVolatility",
    "bootstrap_mean_ci",
    "summarize_fills",
    "git_provenance",
    "parameter_hash",
]
