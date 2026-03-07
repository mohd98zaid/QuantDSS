"""
Signal Consolidation Layer — Phase 3 of Signal Intelligence System

Receives raw candidate signals grouped by symbol from the CandidateSignalPool.
Merges them by direction, handles conflicts, and forwards valid consolidated signals
to the Multi-Strategy Confirmation layer.
"""
from typing import List, Dict, Callable, Awaitable
from collections import defaultdict
import asyncio

from app.core.logging import logger
from app.engine.base_strategy import CandidateSignal


class ConsolidatedSignal:
    """Represents a merged group of CandidateSignals for the same symbol and direction.

    Carries all fields required by downstream layers (FinalAlertGenerator,
    RiskEngine, AutoTrader) so attribute access never fails.
    """

    def __init__(self, primary_signal: CandidateSignal):
        self.symbol_id = primary_signal.symbol_id
        self.symbol_name = primary_signal.symbol_name
        self.signal_type = primary_signal.signal_type
        self.first_timestamp = primary_signal.candle_time
        self.last_timestamp = primary_signal.candle_time

        # ── Price fields from primary signal (CRITICAL-04 fix) ───────
        self.entry_price: float = primary_signal.entry_price
        self.stop_loss: float = primary_signal.stop_loss
        self.target_price: float = primary_signal.target_price
        self.atr_value: float = primary_signal.atr_value

        # Map: strategy_name -> CandidateSignal
        self.contributing_signals: Dict[str, CandidateSignal] = {
            primary_signal.strategy_name: primary_signal,
        }

        # We'll carry forward context from the most recent signal
        self.market_regime = primary_signal.market_regime
        self.relative_strength = primary_signal.relative_strength

        # ── Intelligence pipeline metadata (set by downstream layers) ─
        self.quality_score: float | None = None
        self.total_weight: float = 0.0
        self.ml_probability: float = 0.0
        self.nlp_sentiment: str = "NEUTRAL"

        # ── Risk engine output (set by FinalAlertGenerator) ──────────
        self.risk_quantity: int | None = None
        self.risk_amount: float | None = None

    def merge(self, new_sig: CandidateSignal):
        """Merge another CandidateSignal into this grouped signal."""
        self.contributing_signals[new_sig.strategy_name] = new_sig

        # Update timestamps
        if new_sig.candle_time > self.last_timestamp:
            self.last_timestamp = new_sig.candle_time
            self.market_regime = new_sig.market_regime
            self.relative_strength = new_sig.relative_strength

        if new_sig.candle_time < self.first_timestamp:
            self.first_timestamp = new_sig.candle_time

        # Use tighter stop / wider target if the merging signal is better
        if new_sig.stop_loss and abs(new_sig.entry_price - new_sig.stop_loss) < abs(self.entry_price - self.stop_loss):
            self.stop_loss = new_sig.stop_loss
        if new_sig.target_price and abs(new_sig.target_price - new_sig.entry_price) > abs(self.target_price - self.entry_price):
            self.target_price = new_sig.target_price

    @property
    def confirmation_count(self) -> int:
        return len(self.contributing_signals)

    @property
    def contributing_strategies(self) -> list[str]:
        """Strategy names that contributed to this signal."""
        return list(self.contributing_signals.keys())


class ConsolidationLayer:
    """
    Groups concurrent signals for a symbol. 
    Handles conflicting signals (e.g., BUY and SELL simultaneously).
    """
    
    def __init__(self):
        self._next_layer_callback: Callable[[ConsolidatedSignal], Awaitable[None]] | None = None
        
    def set_callback(self, callback: Callable[[ConsolidatedSignal], Awaitable[None]]):
        """Set the async callback for the next layer (Multi-Strategy Confirmation)."""
        self._next_layer_callback = callback

    async def process_signal_group(self, signals: List[CandidateSignal]):
        """
        Callback from CandidateSignalPool. 
        Receives a time-bounded group of candidate signals for ONE symbol.
        """
        if not signals:
            return

        symbol_id = signals[0].symbol_id
        
        # 1. Group by direction
        by_direction: Dict[str, List[CandidateSignal]] = defaultdict(list)
        for sig in signals:
            by_direction[sig.signal_type].append(sig)
            
        # 2. Conflict Handling
        # If both a BUY and SELL signal fire within the same consolidation window, 
        # it indicates structural chop. Net-zero the signals (invalidate both).
        if "BUY" in by_direction and "SELL" in by_direction:
            logger.warning(
                f"Consolidation Layer: Conflicting signals (BUY/SELL) for symbol_id {symbol_id}. "
                f"Invalidating all {len(signals)} signals in group."
            )
            return

        # 3. Merge signals of the dominant direction
        direction = list(by_direction.keys())[0]
        direction_signals = by_direction[direction]
        
        # Sort chronologically
        direction_signals.sort(key=lambda x: x.candle_time)
        
        consolidated = ConsolidatedSignal(primary_signal=direction_signals[0])
        for sig in direction_signals[1:]:
            consolidated.merge(sig)
            
        logger.info(
            f"Consolidation Layer: Merged {consolidated.confirmation_count} '{direction}' "
            f"signals for symbol_id {symbol_id} into a single ConsolidatedSignal."
        )
            
        # 4. Forward to Multi-Strategy Confirmation layer (Phase 4)
        if self._next_layer_callback:
            try:
                await self._next_layer_callback(consolidated)
            except Exception as e:
                logger.exception(f"Error executing next layer callback from Consolidation: {e}")


# Global Instance
consolidation_layer = ConsolidationLayer()
