"""
MarketClock — discrete-tick simulation clock.

Each tick represents one simulation step (default 100 ms).
"""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class MarketClock:
    tick_ms: int = 100          # milliseconds per tick
    _tick: int   = field(default=0, init=False, repr=False)

    def advance(self) -> int:
        """Advance one tick. Returns the new tick index."""
        self._tick += 1
        return self._tick

    @property
    def tick(self) -> int:
        return self._tick

    @property
    def elapsed_ms(self) -> int:
        return self._tick * self.tick_ms

    @property
    def elapsed_s(self) -> float:
        return self.elapsed_ms / 1000.0

    def reset(self) -> None:
        self._tick = 0
