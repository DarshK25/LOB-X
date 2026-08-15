"""
Stub analytics modules — to be implemented by Person 3.
Placeholders so imports resolve cleanly for integration tests.
"""


def top_n_depth(bids: list[tuple[float, float]], asks: list[tuple[float, float]], n: int) -> dict[str, float]:
    """Compute liquidity (total quantity) in the Top N levels of the book.
    
    bids/asks should be lists of (price, qty) sorted by best price first.
    """
    bid_depth = sum(qty for _, qty in bids[:n])
    ask_depth = sum(qty for _, qty in asks[:n])
    return {
        "bid_depth": bid_depth,
        "ask_depth": ask_depth,
        "total_depth": bid_depth + ask_depth
    }


def compute_vwap(levels: list[tuple[float, float]], target_qty: float) -> float | None:
    """Compute Volume-Weighted Average Price (VWAP) to execute target_qty.
    
    levels: list of (price, qty) sorted by best price first.
    Returns None if there is not enough liquidity.
    """
    if target_qty <= 0:
        return None
        
    cum_qty = 0.0
    cum_vol = 0.0
    
    for price, qty in levels:
        take_qty = min(qty, target_qty - cum_qty)
        cum_qty += take_qty
        cum_vol += price * take_qty
        
        if cum_qty >= target_qty:
            return cum_vol / target_qty
            
    return None # Not enough liquidity
    
    
class LiquidityAnalyzer:
    """Offline analyzer for depth snapshots."""
    
    def __init__(self) -> None:
        self.top5_depths = []
        self.top10_depths = []
        
    def add_snapshot(self, bids: list[tuple[float, float]], asks: list[tuple[float, float]]) -> None:
        t5 = top_n_depth(bids, asks, 5)
        t10 = top_n_depth(bids, asks, 10)
        self.top5_depths.append(t5["total_depth"])
        self.top10_depths.append(t10["total_depth"])
        
    def average_top5_depth(self) -> float:
        if not self.top5_depths:
            return 0.0
        return sum(self.top5_depths) / len(self.top5_depths)
        
    def average_top10_depth(self) -> float:
        if not self.top10_depths:
            return 0.0
        return sum(self.top10_depths) / len(self.top10_depths)
