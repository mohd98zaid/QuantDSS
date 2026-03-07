"""
Signal Quality Score Engine — Phase 5 of Signal Intelligence System

Scores evaluate structural market context independent of strategy triggers. 
A unified score acts as a deterministic barrier before moving the signal to ML/NLP filters.
"""
from typing import Callable, Awaitable

from app.core.logging import logger
from app.engine.consolidation_layer import ConsolidatedSignal
from app.engine.signal_trace import SignalTracer


class QualityScoreLayer:
    """
    Evaluates context: Strategy Alignment, Volume, Trend, VWAP, Relative Strength, Spread.
    Minimum threshold to pass is typically 75/100.
    """
    def __init__(self, threshold: float = 75.0):
        self.threshold = threshold
        self._next_layer_callback: Callable[[ConsolidatedSignal], Awaitable[None]] | None = None

    def set_callback(self, callback: Callable[[ConsolidatedSignal], Awaitable[None]]):
        self._next_layer_callback = callback

    async def score_signal(self, signal: ConsolidatedSignal):
        """
        Calculates a score from 0-100. If score >= threshold, passes signal forward.
        """
        primary = list(signal.contributing_signals.values())[0]
        snap = primary.indicator_snapshot
        
        close = snap.get("close", primary.entry_price)
        
        score = 0.0
        
        # 1. Strategy Alignment (Max 30)
        # total_confirmation_weight was set in Phase 4 (e.g. 1.0, 2.5, etc.)
        weight = getattr(signal, "total_confirmation_weight", 1.0)
        strat_score = min(weight * 10.0, 30.0)
        score += strat_score
        
        # 2. Volume Spike / Quality (Max 20)
        rvol = primary.volume_ratio
        vol_score = 0.0
        if rvol > 2.0:
            vol_score = 20.0
        elif rvol > 1.5:
            vol_score = 10.0
        score += vol_score
        
        # 3. Trend Alignment (Max 15)
        trend_score = 0.0
        ema50 = snap.get("ema_50") or snap.get("ema_trend")
        ema200 = snap.get("ema_200")
        
        if ema50 and ema200:
            if signal.signal_type == "BUY" and close > ema50 > ema200:
                trend_score = 15.0
            elif signal.signal_type == "SELL" and close < ema50 < ema200:
                trend_score = 15.0
        elif ema50:
            if signal.signal_type == "BUY" and close > ema50:
                trend_score = 10.0
            elif signal.signal_type == "SELL" and close < ema50:
                trend_score = 10.0
        score += trend_score
        
        # 4. VWAP Alignment (Max 15)
        vwap_score = 0.0
        vwap = snap.get("vwap")
        if vwap:
            if signal.signal_type == "BUY" and close > vwap:
                vwap_score = 15.0
            elif signal.signal_type == "SELL" and close < vwap:
                vwap_score = 15.0
        score += vwap_score
        
        # 5. Relative Strength (Max 10)
        # If NIFTY index is moving in the same direction, award points
        # primary.relative_strength contains the NIFTY 1h return
        rs_score = 0.0
        index_ret = primary.relative_strength
        if signal.signal_type == "BUY" and index_ret > 0.1:
            rs_score = 10.0
        elif signal.signal_type == "SELL" and index_ret < -0.1:
            rs_score = 10.0
        score += rs_score
        
        # 6. Spread / ATR Ratio (Max 10)
        # Tight spread relative to typical volatility indicates high quality liquidity
        spread_score = 0.0
        spread_pct = primary.spread
        atr_pct = primary.atr_value / primary.entry_price if primary.entry_price > 0 else 0
        
        if spread_pct > 0 and atr_pct > 0:
            ratio = spread_pct / atr_pct
            if ratio < 0.1:  # Spread is less than 10% of ATR
                spread_score = 10.0
            elif ratio < 0.2:
                spread_score = 5.0
        else:
            # If no spread data is available, assume median liquidity (give partial)
            spread_score = 5.0
        score += spread_score
        
        # Final evaluation
        signal.quality_score = score
        
        if score < self.threshold:
            logger.info(
                f"Quality Score Layer: REJECTED {signal.signal_type} for symbol_id {signal.symbol_id}. "
                f"Score {score:.1f} < {self.threshold:.1f} "
                f"[Strat:{strat_score}, Vol:{vol_score}, Trend:{trend_score}, VWAP:{vwap_score}, RS:{rs_score}, Spread:{spread_score}]"
            )
            trace_id = getattr(signal, "_trace_id", "")
            sym_name = getattr(signal, "symbol_name", "?")
            SignalTracer.trace_drop(
                trace_id, "QUALITY_SCORE", sym_name,
                f"score {score:.1f} < {self.threshold:.1f}"
            )
            return
            
        logger.info(
            f"Quality Score Layer: APPROVED {signal.signal_type} for symbol_id {signal.symbol_id}. "
            f"Score {score:.1f} (>= {self.threshold:.1f}) "
            f"[Strat:{strat_score}, Vol:{vol_score}, Trend:{trend_score}, VWAP:{vwap_score}, RS:{rs_score}, Spread:{spread_score}]"
        )
        trace_id = getattr(signal, "_trace_id", "")
        sym_name = getattr(signal, "symbol_name", "?")
        SignalTracer.trace_pass(
            trace_id, "QUALITY_SCORE", sym_name,
            f"score={score:.1f}"
        )
        
        # Forward to ML Layer (Phase 6)
        if self._next_layer_callback:
            try:
                await self._next_layer_callback(signal)
            except Exception as e:
                logger.exception(f"Error executing next layer callback from QualityScore: {e}")

# Global instance
quality_score_layer = QualityScoreLayer()
