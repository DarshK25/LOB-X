"""Market-making strategies: pure functions of (MarketState, position) -> QuotePair."""
from lobx.strategy.avellaneda import AvellanedaStoikovStrategy
from lobx.strategy.base import MarketState, Quote, QuotePair, Strategy
from lobx.strategy.fixed_spread import FixedSpreadStrategy
from lobx.strategy.glft import FairValueModel, GLFTStrategy
from lobx.strategy.inventory_skew import InventorySkewStrategy
from lobx.strategy.no_trade import NoTradeStrategy

__all__ = [
    "AvellanedaStoikovStrategy",
    "FairValueModel",
    "FixedSpreadStrategy",
    "GLFTStrategy",
    "InventorySkewStrategy",
    "MarketState",
    "NoTradeStrategy",
    "Quote",
    "QuotePair",
    "Strategy",
]
