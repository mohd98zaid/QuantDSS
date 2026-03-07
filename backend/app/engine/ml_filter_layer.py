"""
Machine Learning Signal Filter — Phase 6 of Signal Intelligence System

Uses historical performance data to estimate the probabilistic outcome of the signal.
For now, this serves as a placeholder structure until the XGBoost model is trained 
in Shadow Mode using real collected data.
"""
from typing import Callable, Awaitable

from app.core.logging import logger
from app.engine.consolidation_layer import ConsolidatedSignal


class MLFilterLayer:
    """
    Evaluates signal win probability.
    Accepts signals with a win probability > threshold (e.g., 60%).
    """
    def __init__(self, min_probability: float = 0.60):
        self.min_probability = min_probability
        self._next_layer_callback: Callable[[ConsolidatedSignal], Awaitable[None]] | None = None
        self.is_active = False  # Disabled until model is trained

    def set_callback(self, callback: Callable[[ConsolidatedSignal], Awaitable[None]]):
        self._next_layer_callback = callback

    async def evaluate(self, signal: ConsolidatedSignal):
        """
        Predicts win probability. If active and probability < threshold, drops signal.
        """
        if not self.is_active:
            # Shadow Mode: Pass through, and log that ML evaluation is bypassed.
            signal.ml_probability = 0.0
            logger.debug(f"ML Filter Layer: Bypassed for {signal.symbol_id} (Shadow Mode).")
            
            if self._next_layer_callback:
                try:
                    await self._next_layer_callback(signal)
                except Exception as e:
                    logger.exception(f"Error executing next layer callback from ML Filter: {e}")
            return

        # ── Future Implementation ──
        # dmatrix = xgb.DMatrix([[signal.quality_score, primary.volume_ratio, ...]])
        # win_prob = self.model.predict(dmatrix)[0]
        win_prob = 0.65 
        signal.ml_probability = win_prob
        
        if win_prob < self.min_probability:
            logger.info(
                f"ML Filter Layer: REJECTED {signal.signal_type} for symbol_id {signal.symbol_id}. "
                f"Probability {win_prob:.2f} < {self.min_probability:.2f}"
            )
            return
            
        logger.info(
            f"ML Filter Layer: APPROVED {signal.signal_type} for symbol_id {signal.symbol_id}. "
            f"Probability {win_prob:.2f} (>= {self.min_probability:.2f})"
        )
        
        if self._next_layer_callback:
            try:
                await self._next_layer_callback(signal)
            except Exception as e:
                logger.exception(f"Error executing next layer callback from ML Filter: {e}")

# Global instance
ml_filter_layer = MLFilterLayer()
