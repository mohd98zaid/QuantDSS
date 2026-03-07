"""
Signal Trace — Debugging utility for end-to-end signal flow visibility.

Every signal gets a unique trace_id (UUID) that flows through every pipeline
stage.  When something goes silent, grep the logs for the trace_id to find
exactly where a signal was dropped.

Usage:
    from app.engine.signal_trace import SignalTracer
    SignalTracer.trace(trace_id, "CANDLE_CONSUMER", "RELIANCE", "Strategy evaluation started")
"""
import uuid
from app.core.logging import logger


class SignalTracer:
    """Thread-safe signal tracing for debugging pipeline flow."""

    STAGES = [
        "CANDLE_CONSUMER",
        "STRATEGY_EVAL",
        "DEDUP_CHECK",
        "SIGNAL_POOL",
        "CONSOLIDATION",
        "META_STRATEGY",
        "CONFIRMATION",
        "QUALITY_SCORE",
        "REGIME_FILTER",
        "ML_FILTER",
        "NLP_FILTER",
        "TIME_FILTER",
        "LIQUIDITY_FILTER",
        "FINAL_ALERT",
        "RISK_ENGINE",
        "AUTOTRADER_QUEUE",
        "TRADE_EXECUTION",
        "TRADE_EXIT",
    ]

    @staticmethod
    def new_trace_id() -> str:
        """Generate a short unique trace ID for a signal lifecycle."""
        return uuid.uuid4().hex[:12]

    @staticmethod
    def trace(trace_id: str, stage: str, symbol: str, detail: str):
        """
        Log a structured pipeline trace event.

        All trace lines start with [TRACE:xxx] to make them easy to grep.
        """
        logger.info(f"[TRACE:{trace_id}] {stage:20s} | {symbol:15s} | {detail}")

    @staticmethod
    def trace_drop(trace_id: str, stage: str, symbol: str, reason: str):
        """Log a signal drop event — the signal will not proceed further."""
        logger.warning(
            f"[TRACE:{trace_id}] {stage:20s} | {symbol:15s} | ❌ DROPPED: {reason}"
        )

    @staticmethod
    def trace_pass(trace_id: str, stage: str, symbol: str, detail: str = ""):
        """Log a signal passing through a stage successfully."""
        msg = f"[TRACE:{trace_id}] {stage:20s} | {symbol:15s} | ✅ PASSED"
        if detail:
            msg += f" | {detail}"
        logger.info(msg)
