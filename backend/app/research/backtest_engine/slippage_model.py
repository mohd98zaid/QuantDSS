"""
SlippageModel — Configurable slippage models for backtesting.

Models:
  - Fixed: constant percentage slippage
  - Volume-based: slippage increases with fill size relative to market volume
  - Impact: square-root impact model (Almgren-Chriss style)
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class BaseSlippageModel(ABC):
    """Abstract slippage model."""

    @abstractmethod
    def compute(
        self,
        price: float,
        quantity: int,
        signal_type: str,
        volume: int = 0,
    ) -> float:
        """Return the slipped execution price."""
        ...


class FixedSlippage(BaseSlippageModel):
    """Fixed percentage slippage (default: 0.05%)."""

    def __init__(self, pct: float = 0.0005):
        self.pct = pct

    def compute(self, price: float, quantity: int, signal_type: str, volume: int = 0) -> float:
        slip = price * self.pct
        return price + slip if signal_type == "BUY" else price - slip


class VolumeBasedSlippage(BaseSlippageModel):
    """Slippage proportional to fill size vs market volume."""

    def __init__(self, base_pct: float = 0.0005, volume_factor: float = 0.01):
        self.base_pct = base_pct
        self.volume_factor = volume_factor

    def compute(self, price: float, quantity: int, signal_type: str, volume: int = 0) -> float:
        if volume > 0:
            participation = quantity / volume
            pct = self.base_pct + (participation * self.volume_factor)
        else:
            pct = self.base_pct

        slip = price * pct
        return price + slip if signal_type == "BUY" else price - slip


class ImpactSlippage(BaseSlippageModel):
    """Square-root market impact model."""

    def __init__(self, impact_coefficient: float = 0.1):
        self.impact_coefficient = impact_coefficient

    def compute(self, price: float, quantity: int, signal_type: str, volume: int = 0) -> float:
        if volume > 0:
            import math
            participation = quantity / volume
            impact_pct = self.impact_coefficient * math.sqrt(participation)
        else:
            impact_pct = 0.001

        slip = price * impact_pct
        return price + slip if signal_type == "BUY" else price - slip
