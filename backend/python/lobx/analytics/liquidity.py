"""
Stub analytics modules — to be implemented by Person 3.
Placeholders so imports resolve cleanly for integration tests.
"""


def liquidity_score(bid_qty: int, ask_qty: int, spread: int) -> float:
    """Simple liquidity proxy: depth / spread."""
    if spread == 0:
        return float("inf")
    return (bid_qty + ask_qty) / spread


def order_flow_imbalance(bid_qty: int, ask_qty: int) -> float:
    """Order flow imbalance in [-1, 1]. +1 = all bids, -1 = all asks."""
    total = bid_qty + ask_qty
    if total == 0:
        return 0.0
    return (bid_qty - ask_qty) / total


def market_impact_estimate(trade_qty: int, total_qty: int, spread: int) -> float:
    """Linear market impact approximation: η * (qty / total_depth) * spread."""
    if total_qty == 0:
        return 0.0
    return (trade_qty / total_qty) * spread * 0.5
