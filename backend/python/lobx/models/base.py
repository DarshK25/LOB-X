"""Base class for all quant models."""
from __future__ import annotations
from abc import ABC, abstractmethod


class BaseModel(ABC):
    """Common interface for all quantitative models."""

    @abstractmethod
    def update(self, **kwargs: float) -> None:
        """Ingest new market data or state."""
        ...
