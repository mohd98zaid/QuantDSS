"""
StrategyRunner — Orchestrates strategy evaluation across symbols.
On each candle close, evaluates all active strategies for the symbol.
"""

import pandas as pd

from app.core.logging import logger
from app.engine.base_strategy import BaseStrategy, CandidateSignal
from app.engine.strategies.ema_crossover import EMACrossoverStrategy
from app.engine.strategies.rsi_mean_reversion import RSIMeanReversionStrategy
from app.engine.strategies.orb_vwap import ORBVWAPStrategy
from app.engine.strategies.volume_expansion import VolumeExpansionStrategy
from app.engine.strategies.trend_continuation import TrendContinuationStrategy
from app.engine.strategies.vwap_reclaim import VWAPReclaimStrategy
from app.engine.strategies.relative_strength_strategy import RelativeStrengthStrategy
from app.engine.strategies.trend_pullback import TrendPullbackStrategy
from app.engine.strategies.failed_breakout import FailedBreakoutStrategy

# Registry mapping strategy type key → class
# FIX #2: added the 3 new strategies that were built but never wired into the live pipeline
STRATEGY_REGISTRY: dict[str, type[BaseStrategy]] = {
    "trend_following":    EMACrossoverStrategy,
    "ema_crossover":      EMACrossoverStrategy,
    "mean_reversion":     RSIMeanReversionStrategy,
    "rsi_mean_reversion": RSIMeanReversionStrategy,
    "orb_vwap":           ORBVWAPStrategy,
    "volume_expansion":   VolumeExpansionStrategy,
    "trend_continuation": TrendContinuationStrategy,
    "vwap_reclaim":       VWAPReclaimStrategy,
    "relative_strength":  RelativeStrengthStrategy,
    "trend_pullback":     TrendPullbackStrategy,
    "failed_breakout":    FailedBreakoutStrategy,
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

    async def evaluate(
        self, candles: pd.DataFrame, symbol_id: int
    ) -> list[CandidateSignal]:
        """
        Evaluate all loaded strategies against the latest candles for a symbol.

        Args:
            candles: OHLCV DataFrame for the symbol
            symbol_id: Database ID of the symbol

        Returns:
            List of CandidateSignal objects (may be empty)
        """
        from app.engine.strategy_health import strategy_health_monitor
        signals = []

        for strategy_id, strategy in self._strategies.items():
            try:
                # Fix Group 6: Strategy Health Enforcement
                if await strategy_health_monitor.is_disabled(strategy_id):
                    logger.debug(f"StrategyRunner: Skipping disabled strategy {strategy_id}")
                    continue

                signal = strategy.evaluate(candles, symbol_id)
                if signal is not None:
                    signals.append(signal)
                    logger.info(
                        f"Signal from strategy {strategy_id}: "
                        f"{signal.signal_type} @ {signal.entry_price:.2f}"
                    )
            except Exception as e:
                logger.exception(f"Strategy {strategy_id} evaluation error: {e}")

        return signals

    @property
    def loaded_strategies(self) -> list[int]:
        """Return list of loaded strategy IDs."""
        return list(self._strategies.keys())
