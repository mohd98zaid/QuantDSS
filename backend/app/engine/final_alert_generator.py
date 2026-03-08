"""
Final Alert Generator — Terminus of the Signal Intelligence System

Receives fully verified signals from the Time Filter, runs them through
the Risk Engine for mandatory validation, persists results, alerts the UI
via SSE, and pushes APPROVED signals to the AutoTrader queue.

Corrective Refactor:
  - Risk Engine is now MANDATORY before any signal reaches AutoTrader
  - Blocked/Skipped signals are persisted with rejection reasons
  - Direct _auto_trade_hook import removed — uses AutoTrader queue directly
  - SignalTracer records every stage transition
"""
import json
from datetime import date, datetime, timezone, timedelta

from sqlalchemy import select

from app.core.logging import logger
from app.engine.consolidation_layer import ConsolidatedSignal
from app.engine.signal_trace import SignalTracer

IST = timezone(timedelta(hours=5, minutes=30))


# ── Database Persistence ─────────────────────────────────────────────────────

async def _persist_signal_to_db(
    signal: ConsolidatedSignal,
    status: str = "APPROVED",
    block_reason: str | None = None,
    source: str = "intelligence_pipeline",
    risk_quantity: int | None = None,
    risk_amount: float | None = None,
):
    """
    Persist a signal to the signals DB table with full intelligence metadata.
    Writes to first-class columns instead of burying data in JSON.
    """
    try:
        from app.core.database import async_session_factory
        from app.models.signal import Signal as SignalModel
        from app.models.symbol import Symbol

        async with async_session_factory() as db:
            # Try to resolve symbol_id from symbol_name
            symbol_id = None
            sym_name = getattr(signal, "symbol_name", None) or ""
            if sym_name:
                result = await db.execute(
                    select(Symbol.id).where(Symbol.trading_symbol == sym_name).limit(1)
                )
                row = result.scalar_one_or_none()
                if row:
                    symbol_id = row

            # Build score breakdown for UI
            score_breakdown = json.dumps({
                "quality_score": getattr(signal, "quality_score", None),
                "total_weight": getattr(signal, "total_weight", 0),
                "source": source,
                "risk_quantity": risk_quantity,
                "risk_amount": risk_amount,
            })

            # Compute R:R safely
            risk_reward = 0.0
            if signal.entry_price and signal.stop_loss:
                sl_dist = abs(signal.entry_price - signal.stop_loss)
                if sl_dist > 0.01:
                    risk_reward = round(
                        abs(signal.target_price - signal.entry_price) / sl_dist, 2
                    )

            db.add(SignalModel(
                symbol_id=symbol_id,
                signal_type=signal.signal_type,
                entry_price=signal.entry_price,
                stop_loss=signal.stop_loss,
                target_price=signal.target_price,
                risk_reward=risk_reward,
                atr_value=signal.atr_value,
                confidence_score=int(getattr(signal, "quality_score", 0) or 0),
                confidence_tier=(
                    "HIGH" if (getattr(signal, "quality_score", 0) or 0) >= 75
                    else "MEDIUM" if (getattr(signal, "quality_score", 0) or 0) >= 50
                    else "REJECT"
                ),
                risk_status=status,
                block_reason=block_reason,
                score_breakdown=score_breakdown,
                candle_time=getattr(signal, "first_timestamp", None),
                # First-class columns for intelligence pipeline
                ml_probability=getattr(signal, "ml_probability", None),
                sentiment=getattr(signal, "nlp_sentiment", None),
                strategies_confirmed=json.dumps(
                    getattr(signal, "contributing_strategies", [])
                ),
            ))
            await db.commit()

        logger.debug(f"Signal persisted: {sym_name} {signal.signal_type} [{status}]")

    except Exception as e:
        logger.warning(f"_persist_signal_to_db failed (non-critical): {e}")


# ── Risk Engine Validation ───────────────────────────────────────────────────

# Risk validation has been moved entirely to RiskEngineWorker (Fix Group 3)


# ── Main Class ───────────────────────────────────────────────────────────────

class FinalAlertGenerator:
    """
    The final terminus of the Signal Intelligence System pipeline.

    Receives fully approved signals from the intelligence layers, runs them
    through the Risk Engine (MANDATORY), and only forwards APPROVED signals
    to the AutoTrader queue.
    """

    async def process_alert(self, signal: ConsolidatedSignal):
        """
        Process a fully verified signal from the intelligence pipeline.

        1. Run through Risk Engine (MANDATORY gate)
        2. If APPROVED: persist, SSE publish, enqueue for AutoTrader
        3. If BLOCKED/SKIPPED: persist rejection, log, return
        """
        sym_name = getattr(signal, "symbol_name", "UNKNOWN")
        trace_id = getattr(signal, "_trace_id", None) or ""

        SignalTracer.trace(
            trace_id, "FINAL_ALERT", sym_name,
            f"{signal.signal_type} @ ₹{signal.entry_price:.2f}"
        )

        # ── Pipeline Terminus ─────────────────────────────────────────────
        # Signals are no longer risk-checked here.
        # They are marked PENDING_RISK and published to Redis for the Risk Worker.
        try:
            signal.risk_quantity = None
            signal.risk_amount = None
            
            SignalTracer.trace_pass(
                trace_id, "PIPELINE_COMPLETE", sym_name, "Publishing to signals:approved"
            )
        except Exception as e:
            logger.exception(f"FinalAlertGenerator: Pipeline completion error for {sym_name}: {e}")
            return

        logger.info(
            f"🚀 FINAL ALERT: {sym_name} {signal.signal_type} "
            f"@ ₹{signal.entry_price:.2f} → ₹{signal.target_price:.2f} "
            f"(SL ₹{signal.stop_loss:.2f}) "
            f"Score={getattr(signal, 'quality_score', 'N/A')} "
            f"ML={getattr(signal, 'ml_probability', 'N/A')} "
            f"Sentiment={getattr(signal, 'nlp_sentiment', 'N/A')} "
            f"Qty={risk_decision.quantity}"
        )

        # 1. Persist approved signal to DB as PENDING_RISK
        await _persist_signal_to_db(
            signal, status="PENDING_RISK",
        )

        # 2. Publish to SSE stream for UI
        try:
            from app.alerts.sse_manager import SSEManager
            await SSEManager.publish_signal_event({
                "signal_type": signal.signal_type,
                "symbol": sym_name,
                "entry_price": float(signal.entry_price),
                "stop_loss": float(signal.stop_loss),
                "target_price": float(signal.target_price),
                "quality_score": getattr(signal, "quality_score", None),
                "ml_probability": getattr(signal, "ml_probability", None),
                "sentiment": getattr(signal, "nlp_sentiment", None),
                "strategies_confirmed": getattr(signal, "contributing_strategies", []),
                "risk_status": "PENDING_RISK",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
        except Exception as e:
            logger.debug(f"SSE publish failed for {sym_name}: {e}")

        # 3. Publish to Redis for Risk Worker
        try:
            from app.core.streams import publish_to_stream, STREAM_SIGNALS_APPROVED
            import json
            
            message = {
                "symbol_id": str(getattr(signal, "symbol_id", "0")),
                "symbol_name": sym_name,
                "signal_type": signal.signal_type,
                "entry_price": str(signal.entry_price),
                "stop_loss": str(signal.stop_loss),
                "target_price": str(signal.target_price),
                "atr_value": str(signal.atr_value),
                "candle_time": getattr(signal, "first_timestamp", datetime.now(timezone.utc)).isoformat(),
                "contributing_strategies": json.dumps(getattr(signal, "contributing_strategies", [])),
                "quality_score": str(getattr(signal, "quality_score", "0")),
                "_trace_id": trace_id,
            }
            await publish_to_stream(STREAM_SIGNALS_APPROVED, message)
        except Exception as e:
            logger.exception(f"FinalAlertGenerator: Redis publish failed for {sym_name}: {e}")


# Public helper for rejection logging from upstream layers
async def log_rejected_signal(
    signal: ConsolidatedSignal,
    reason: str,
    layer: str,
):
    """
    Called by upstream intelligence layers (consolidation, confirmation,
    quality score, time filter) to log rejected signals to the DB.
    """
    logger.info(f"Signal REJECTED by {layer}: {getattr(signal, 'symbol_name', '?')} — {reason}")
    await _persist_signal_to_db(
        signal,
        status="BLOCKED",
        block_reason=f"{layer}: {reason}",
    )


# Module-level singleton
final_alert_layer = FinalAlertGenerator()
