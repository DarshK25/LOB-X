"""
Market data generator — produces synthetic order flow for testing/simulation.

When real LOBSTER data is not available, this generates a realistic order
stream: Poisson arrivals, log-normal price moves, exponential sizes.
"""
from __future__ import annotations
import random
import math
import lobx_cpp


class SyntheticOrderGenerator:
    """
    Generates a stream of random orders similar to real LOB data.

    Parameters
    ----------
    mid_price       : initial mid price in ticks
    arrival_rate    : orders per tick (Poisson mean)
    volatility      : per-tick price std in ticks
    seed            : RNG seed
    """

    def __init__(
        self,
        mid_price: int,
        arrival_rate: float = 2.0,
        volatility: float   = 1.0,
        seed: int | None    = None,
    ) -> None:
        self._mid    = float(mid_price)
        self._rate   = arrival_rate
        self._vol    = volatility
        self._rng    = random.Random(seed)
        self._next_id = 1

    def tick(self) -> list[lobx_cpp.Order]:
        """Generate a batch of orders for one tick."""
        n_orders = max(0, round(self._rng.gauss(self._rate, math.sqrt(self._rate))))
        orders   = []
        for _ in range(n_orders):
            # Random walk mid
            self._mid += self._rng.gauss(0, self._vol)
            offset = self._rng.randint(-5, 5)
            price  = max(1, round(self._mid) + offset)
            qty    = max(1, round(abs(self._rng.gauss(10, 5))))
            is_buy = self._rng.random() < 0.5
            orders.append(lobx_cpp.Order(self._next_id, price, qty, is_buy))
            self._next_id += 1
        return orders

    @property
    def mid(self) -> float:
        return self._mid
