"""Market data sub-package — synthetic generator + live Binance pipeline."""
from lobx import LOBX_CPP_AVAILABLE

# SyntheticOrderGenerator requires the C++ engine (lobx_cpp).
# Import it only when the engine is available so pure-Python pipeline
# modules (depth_stream_manager, trade_stream, collector) can be used
# on machines where the C++ extension hasn't been compiled yet.
if LOBX_CPP_AVAILABLE:
    from lobx.market_data.generator import SyntheticOrderGenerator  # noqa: F401

from lobx.market_data.binance_ws_client import BinanceWSClient
from lobx.market_data.depth_stream_manager import DepthStreamManager, LocalOrderBook
from lobx.market_data.trade_stream import TradeTick, parse_trade_event
from lobx.market_data.collector import Collector

__all__ = [
    # Existing (C++ required)
    "SyntheticOrderGenerator",
    # Phase 1 live pipeline (pure Python)
    "BinanceWSClient",
    "DepthStreamManager",
    "LocalOrderBook",
    "TradeTick",
    "parse_trade_event",
    "Collector",
]
