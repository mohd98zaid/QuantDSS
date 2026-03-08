"""
BaseStrategy — Abstract interface for all trading strategies.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd


from typing import Dict, Any, List

@dataclass
class CandidateSignal:
    """Phase 1: Standardized candidate signal emitted by strategies."""
    symbol_id: int
    strategy_id: int
    strategy_name: str
    signal_type: str       # BUY / SELL
    entry_price: float
    stop_loss: float
    target_price: float
    atr_value: float
    candle_time: datetime
    confidence_score: float = 0.0   # 0-100
    
    # Phase 1 specific fields
    symbol_name: str = ""
    strategies: List[str] = field(default_factory=list)
    base_strategy: str = ""
    indicator_snapshot: Dict[str, Any] = field(default_factory=dict)
    market_snapshot: Dict[str, Any] = field(default_factory=dict)
    volume_ratio: float = 1.0
    spread: float = 0.0
    relative_strength: float = 0.0
    market_regime: str | None = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.base_strategy:
            self.base_strategy = self.strategy_name
        if not self.strategies:
            self.strategies = [self.strategy_name]

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
    def evaluate(self, candles: pd.DataFrame, symbol_id: int) -> CandidateSignal | None:
        """
        Evaluate strategy conditions on the latest candles.

        Args:
            candles: DataFrame with columns [time, open, high, low, close, volume]
                     Plus any indicator columns added by IndicatorEngine.
            symbol_id: Database ID of the symbol being evaluated.

        Returns:
            CandidateSignal if entry conditions are met, else None.
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
        return 500  # Default: 500 candles (Fix 4: Warmup correction)


# ── Backward-compat alias ──────────────────────────────────────────
# Several modules (risk_engine, signal_pipeline, auto_trader_engine,
# alert_dispatcher, telegram_notifier, tests) import "RawSignal".
# The class was renamed to CandidateSignal during Phase 1 refactoring
# but the old name was never aliased, causing ImportError at startup.
RawSignal = CandidateSignal
