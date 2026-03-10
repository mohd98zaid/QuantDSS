"""
Multi-Strategy Confirmation Layer — Phase 4 of Signal Intelligence System

Filters ConsolidatedSignals based on multi-strategy alignment. A single strategy generating
a signal is weak; multiple strategies confirming the same direction is strong.
"""
from typing import Callable, Awaitable

from app.core.logging import logger
from app.engine.consolidation_layer import ConsolidatedSignal
from app.engine.signal_trace import SignalTracer


class ConfirmationLayer:
    """
    Evaluates the 'weight' of the contributing strategies in a ConsolidatedSignal.
    Only allows signals that meet a minimum confirmation threshold through to the next phase.
    """
    
    def __init__(self, min_confirmations: float = 1.5):
        self.min_confirmations = min_confirmations
        self._next_layer_callback: Callable[[ConsolidatedSignal], Awaitable[None]] | None = None
        
        # Optional weighting for strategies. Core strategies hold more weight.
        # This acts as a multiplier. By default, any unknown strategy has weight 1.0.
        self.strategy_weights = {
            "vwap": 1.5,
            "trend": 1.2,
            "ema": 1.0,
            "rsi": 0.8,
            "volume": 1.0,
            "orb": 1.5,
        }

    def set_callback(self, callback: Callable[[ConsolidatedSignal], Awaitable[None]]):
        """Set the async callback for the next layer (Phase 5: Score Engine)."""
        self._next_layer_callback = callback

    async def verify_confirmation(self, signal: ConsolidatedSignal):
        """
        Callback from ConsolidationLayer.
        Calculates total strategy alignment weight and drops weak signals.
        """
        total_weight = 0.0
        
        for strat_name, sig in signal.contributing_signals.items():
            # If any contributing signal came from the manual scanner, bypass weight requirement
            is_scanner = getattr(sig, "metadata", {}).get("source") == "scanner"
            if is_scanner:
                logger.debug(f"Confirmation Layer: Bypassing weight check for scanner-sourced signal on {signal.symbol_id}")
                total_weight = max(total_weight, self.min_confirmations)
                break

            # Derive score heavily tied to our standard names
            strat_lower = strat_name.lower()
            matched_weight = 1.0
            
            for key_term, weight in self.strategy_weights.items():
                if key_term in strat_lower:
                    matched_weight = weight
                    break
                    
            total_weight += matched_weight
            
        if total_weight < self.min_confirmations:
            logger.info(
                f"Confirmation Layer: REJECTED {signal.signal_type} for symbol_id {signal.symbol_id}. "
                f"Weight {total_weight:.1f} < {self.min_confirmations:.1f}. "
                f"Strategies: {list(signal.contributing_signals.keys())}"
            )
            trace_id = getattr(signal, "_trace_id", "")
            sym_name = getattr(signal, "symbol_name", "?")
            SignalTracer.trace_drop(
                trace_id, "CONFIRMATION", sym_name,
                f"weight {total_weight:.1f} < {self.min_confirmations:.1f}"
            )
            return
            
        logger.info(
            f"Confirmation Layer: CONFIRMED {signal.signal_type} for symbol_id {signal.symbol_id}. "
            f"Weight {total_weight:.1f} (>= {self.min_confirmations:.1f})."
        )
        trace_id = getattr(signal, "_trace_id", "")
        sym_name = getattr(signal, "symbol_name", "?")
        SignalTracer.trace_pass(
            trace_id, "CONFIRMATION", sym_name,
            f"weight={total_weight:.1f}"
        )
        
        # Signal carries its confirmation weight as a new property for downstream use
        signal.total_confirmation_weight = total_weight
        
        # Forward to Phase 5: Signal Quality Score Engine
        if self._next_layer_callback:
            try:
                await self._next_layer_callback(signal)
            except Exception as e:
                logger.exception(f"Error executing next layer callback from Confirmation: {e}")


# Global instance
confirmation_layer = ConfirmationLayer()
