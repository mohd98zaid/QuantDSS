"""
StrategyRunner — Orchestrates strategy evaluation across symbols.
On each candle close, evaluates all active strategies for the symbol.
"""

import pandas as pd

from app.core.logging import logger
from app.engine.base_strategy import BaseStrategy, RawSignal
from app.engine.strategies.ema_crossover import EMACrossoverStrategy
from app.engine.strategies.rsi_mean_reversion import RSIMeanReversionStrategy

# Registry mapping strategy type to class
STRATEGY_REGISTRY: dict[str, type[BaseStrategy]] = {
    "trend_following": EMACrossoverStrategy,
    "mean_reversion": RSIMeanReversionStrategy,
}


class StrategyRunner:
    """
    Orchestrates strategy evaluation.
    On each candle close, evaluates all loaded strategies for the symbol.
    """

    def __init__(self):
        self._strategies: dict[int, BaseStrategy] = {}  # strategy_id → instance

    def load_strategy(self, strategy_id: int, strategy_type: str, params: dict) -> None:
        """Load a strategy instance from DB configuration."""
        strategy_cls = STRATEGY_REGISTRY.get(strategy_type)
        if not strategy_cls:
            logger.warning(f"Unknown strategy type: {strategy_type}")
            return

        self._strategies[strategy_id] = strategy_cls(
            strategy_id=strategy_id,
            params=params,
        )
        logger.info(f"Loaded strategy: {strategy_type} (id={strategy_id})")

    def unload_strategy(self, strategy_id: int) -> None:
        """Remove a strategy from the runner."""
        self._strategies.pop(strategy_id, None)

    def evaluate(
        self, candles: pd.DataFrame, symbol_id: int
    ) -> list[RawSignal]:
        """
        Evaluate all loaded strategies against the latest candles for a symbol.

        Args:
            candles: OHLCV DataFrame for the symbol
            symbol_id: Database ID of the symbol

        Returns:
            List of RawSignal objects (may be empty)
        """
        signals = []

        for strategy_id, strategy in self._strategies.items():
            try:
                signal = strategy.evaluate(candles, symbol_id)
                if signal is not None:
                    signals.append(signal)
                    logger.info(
                        f"Signal from strategy {strategy_id}: "
                        f"{signal.signal_type} @ {signal.entry_price:.2f}"
                    )
            except Exception as e:
                logger.error(f"Strategy {strategy_id} evaluation error: {e}")

        return signals

    @property
    def loaded_strategies(self) -> list[int]:
        """Return list of loaded strategy IDs."""
        return list(self._strategies.keys())
