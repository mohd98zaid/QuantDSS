"""
BaseStrategy — Abstract interface for all trading strategies.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

import pandas as pd


@dataclass
class RawSignal:
    """Raw signal output from a strategy before risk validation."""
    symbol_id: int
    strategy_id: int
    signal_type: str       # BUY / SELL / EXIT
    entry_price: float
    stop_loss: float
    target_price: float
    atr_value: float
    candle_time: datetime

    @property
    def risk_reward(self) -> float:
        """Calculate risk-reward ratio."""
        if self.signal_type == "BUY":
            risk = self.entry_price - self.stop_loss
            reward = self.target_price - self.entry_price
        else:  # SELL
            risk = self.stop_loss - self.entry_price
            reward = self.entry_price - self.target_price

        if risk <= 0:
            return 0.0
        return round(reward / risk, 2)


class BaseStrategy(ABC):
    """
    Abstract base class for all trading strategies.

    All strategies receive a DataFrame of candles and return either:
    - A RawSignal (entry conditions met)
    - None (no signal)

    The strategy must NOT look ahead — only candles up to the current bar.
    """

    def __init__(self, strategy_id: int, params: dict):
        self.strategy_id = strategy_id
        self.params = params

    @abstractmethod
    def evaluate(self, candles: pd.DataFrame, symbol_id: int) -> RawSignal | None:
        """
        Evaluate strategy conditions on the latest candles.

        Args:
            candles: DataFrame with columns [time, open, high, low, close, volume]
                     Plus any indicator columns added by IndicatorEngine.
            symbol_id: Database ID of the symbol being evaluated.

        Returns:
            RawSignal if entry conditions are met, else None.
        """
        pass

    @property
    @abstractmethod
    def strategy_type(self) -> str:
        """Return the strategy type string for indicator computation."""
        pass

    @property
    def min_candles_required(self) -> int:
        """Minimum number of candles needed for reliable indicator computation."""
        return 100  # Default: 100 candles
