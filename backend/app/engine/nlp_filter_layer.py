"""
NLP News Sentiment Filter — Phase 7 of Signal Intelligence System

Removes technical signals that conflict with fundamental macroeconomic
or company-level news.
"""
from typing import Callable, Awaitable

from app.core.logging import logger
from app.engine.consolidation_layer import ConsolidatedSignal


class NLPFilterLayer:
    """
    Evaluates news sentiment.
    If Long Signal, Sentiment must be POSITIVE or NEUTRAL.
    If NEGATIVE sentiment confidence > 80%, Reject Signal.
    """
    def __init__(self):
        self._next_layer_callback: Callable[[ConsolidatedSignal], Awaitable[None]] | None = None
        self.is_active = False  # Disabled until FinBERT API is hooked up

    def set_callback(self, callback: Callable[[ConsolidatedSignal], Awaitable[None]]):
        self._next_layer_callback = callback

    async def evaluate(self, signal: ConsolidatedSignal):
        """
        Check sentiment, drop signal if contraindicated.
        """
        if not self.is_active:
            # Shadow Mode: Pass through
            signal.nlp_sentiment = "NEUTRAL"
            logger.debug(f"NLP Filter Layer: Bypassed for {signal.symbol_id} (Shadow Mode).")
            
            if self._next_layer_callback:
                try:
                    await self._next_layer_callback(signal)
                except Exception as e:
                    logger.exception(f"Error executing next layer callback from NLP Filter: {e}")
            return

        # ── Future Implementation ──
        # sentiment, confidence = fetch_and_analyze_news(signal.symbol_id)
        sentiment = "NEUTRAL"
        confidence = 0.5
        signal.sentiment = sentiment
        
        if sentiment == "NEGATIVE" and confidence > 0.8:
            logger.info(
                f"NLP Filter Layer: REJECTED {signal.signal_type} for symbol_id {signal.symbol_id}. "
                f"High-confidence NEGATIVE news."
            )
            return
            
        logger.info(
            f"NLP Filter Layer: APPROVED {signal.signal_type} for symbol_id {signal.symbol_id}. "
            f"Sentiment: {sentiment}"
        )
        
        if self._next_layer_callback:
            try:
                await self._next_layer_callback(signal)
            except Exception as e:
                logger.exception(f"Error executing next layer callback from NLP Filter: {e}")


# Global instance
nlp_filter_layer = NLPFilterLayer()
