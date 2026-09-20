"""
Probabilistic queue position models for order execution simulation.

This module implements the HFTENGINE-style probabilistic queue position model
for realistic market-making simulation. The model uses DETERMINISTIC expected-
position calculation, not random sampling.

Key Semantics (matching HFTENGINE exactly):
--------------------------------------------
1. ORDER SUBMISSION:
   - front_qty = total visible quantity at price level
   - Conservative assumption: order joins BACK of queue
   
2. TRADE AT PRICE:
   - Unconditional queue consumption: front_qty -= trade_qty
   - Accumulate trades: cum_trade_qty += trade_qty (to avoid double-counting)
   
3. DEPTH INCREASE:
   - New orders assumed to join behind us
   - front_qty unchanged
   
4. DEPTH DECREASE:
   - Calculate net change: chg = prev_qty - new_qty - cum_trade_qty
   - Use probability model to estimate advancement
   - Formula: est_front = front - (1-prob)*chg + min(0, back - prob*chg)
   
5. FILL DECISION:
   - Order fills when front_qty <= 0

References:
-----------
* https://quant.stackexchange.com/questions/3782/
* https://rigtorp.se/2013/06/08/estimating-order-queue-position.html
* HFTENGINE: _reference/hftengine/hftbacktest/src/backtest/models/queue.rs
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Protocol


class QueueProbabilityModel(Protocol):
    """Protocol for queue advancement probability functions."""
    
    def prob(self, front_qty: float, back_qty: float) -> float:
        """
        Calculate probability of queue advancement on depth decrease.
        
        Args:
            front_qty: Estimated quantity ahead of order in queue
            back_qty: Estimated quantity behind order in queue
            
        Returns:
            Probability in [0, 1] that order advances when level decreases
        """
        ...


@dataclass
class PowerProbFunc3:
    """
    Power function probability model: prob = 1 - (front/(front+back))^n
    
    This is the default HFTENGINE model (PowerProbQueueFunc3).
    
    Args:
        n: Exponent controlling queue advancement probability
           - n=1: Linear proportional to position
           - n>1: More conservative (lower advancement probability)
           - n=3: HFTENGINE default
    """
    n: float = 3.0
    
    def __post_init__(self):
        if self.n <= 0:
            raise ValueError(f"n must be positive, got {self.n}")
    
    def prob(self, front_qty: float, back_qty: float) -> float:
        """Calculate queue advancement probability."""
        if front_qty < 0 or back_qty < 0:
            raise ValueError(f"Quantities must be non-negative: front={front_qty}, back={back_qty}")
        
        total = front_qty + back_qty
        if total <= 0:
            return 1.0  # Empty queue, immediate advancement
        
        # prob = 1 - (front/total)^n
        ratio = front_qty / total
        prob = 1.0 - (ratio ** self.n)
        
        # Clamp to [0, 1]
        return max(0.0, min(1.0, prob))


@dataclass
class QueuePosition:
    """
    Tracks queue position for a resting order.
    
    Attributes:
        front_qty: Estimated quantity ahead of order
        cum_trade_qty: Accumulated trade quantity since last depth update
    """
    front_qty: float = 0.0
    cum_trade_qty: float = 0.0
    
    def __post_init__(self):
        if self.front_qty < 0:
            self.front_qty = 0.0


class ProbabilisticQueueModel:
    """
    Probabilistic queue position model matching HFTENGINE semantics.
    
    Uses deterministic expected-position calculation (NOT random sampling).
    The probability from the model is used to calculate expected queue
    advancement when depth decreases, not to make a random decision.
    
    This matches HFTENGINE's ProbQueueModel behavior exactly.
    
    Args:
        prob_model: Probability function (default: PowerProbFunc3(n=3))
        seed: DEPRECATED - model is deterministic, seed has no effect
    """
    
    def __init__(
        self,
        prob_model: QueueProbabilityModel | None = None,
        seed: int | None = None,
    ):
        self.prob_model = prob_model or PowerProbFunc3(n=3.0)
        # Seed is kept for backward compatibility but not used
        if seed is not None:
            import warnings
            warnings.warn(
                "ProbabilisticQueueModel is deterministic; seed parameter is ignored",
                DeprecationWarning,
                stacklevel=2
            )
        self._rng = None  # No RNG needed for deterministic model
    
    def initialize_position(self, level_qty: float) -> QueuePosition:
        """
        Initialize queue position when order first joins the level.
        
        Conservative assumption: order is at the back of the queue.
        
        Args:
            level_qty: Total visible quantity at price level
            
        Returns:
            Initial queue position
        """
        return QueuePosition(front_qty=max(0.0, level_qty), cum_trade_qty=0.0)
    
    def on_trade(self, pos: QueuePosition, trade_qty: float) -> None:
        """
        Update queue position when trade occurs at same price level.
        
        Trade consumption reduces queue ahead unconditionally.
        Track cumulative trades to avoid double-counting with depth changes.
        
        Args:
            pos: Current queue position (modified in-place)
            trade_qty: Quantity traded
        """
        if trade_qty < 0:
            raise ValueError(f"Trade quantity must be non-negative: {trade_qty}")
        
        pos.front_qty = max(0.0, pos.front_qty - trade_qty)
        pos.cum_trade_qty += trade_qty
    
    def on_depth_change(
        self,
        pos: QueuePosition,
        prev_level_qty: float,
        new_level_qty: float,
    ) -> None:
        """
        Update queue position when depth changes at price level.
        
        Handles both increases and decreases, accounting for trades
        that occurred since last depth update.
        
        Args:
            pos: Current queue position (modified in-place)
            prev_level_qty: Previous level quantity
            new_level_qty: New level quantity
        """
        # Calculate net change, subtracting already-counted trades
        change = prev_level_qty - new_level_qty
        change -= pos.cum_trade_qty
        pos.cum_trade_qty = 0.0  # Reset trade accumulator
        
        # Depth increase: queue ahead unchanged (new orders join back)
        if change < 0:
            pos.front_qty = min(pos.front_qty, new_level_qty)
            return
        
        # Depth decrease: probabilistic queue advancement
        front = pos.front_qty
        back = prev_level_qty - front
        
        prob = self.prob_model.prob(front, back)
        
        # Calculate expected position after decrease
        est_front = front - (1.0 - prob) * change + min(0.0, back - prob * change)
        pos.front_qty = max(0.0, min(est_front, new_level_qty))
    
    def is_filled(self, pos: QueuePosition) -> bool:
        """
        Determine if order should be filled based on queue position.
        
        Order fills when queue ahead is exhausted (front_qty <= 0).
        
        Args:
            pos: Current queue position
            
        Returns:
            True if order should fill
        """
        return pos.front_qty <= 0.0
    
    def reset_seed(self, seed: int) -> None:
        """DEPRECATED: Model is deterministic, no seed needed."""
        import warnings
        warnings.warn(
            "ProbabilisticQueueModel is deterministic; reset_seed has no effect",
            DeprecationWarning,
            stacklevel=2
        )
