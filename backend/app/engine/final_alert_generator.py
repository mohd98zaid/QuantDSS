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

async def _validate_with_risk_engine(signal: ConsolidatedSignal):
    """
    Load RiskConfig, DailyRiskState, and Portfolio from DB, then call
    RiskEngine.validate(). Returns the RiskDecision.
    """
    from app.core.database import async_session_factory
    from app.models.risk_config import RiskConfig
    from app.models.daily_risk_state import DailyRiskState
    from app.models.paper_trade import PaperTrade
    from app.models.live_trade import LiveTrade
    from app.engine.risk_engine import RiskEngine, Portfolio
    from app.engine.base_strategy import RawSignal
    from sqlalchemy import func

    async with async_session_factory() as db:
        # Load risk config
        rc = await db.execute(select(RiskConfig).limit(1))
        risk_cfg = rc.scalar_one_or_none()

        if not risk_cfg:
            # No risk config — cannot validate, create a default decision
            from dataclasses import dataclass

            @dataclass
            class _DefaultReject:
                status: str = "BLOCKED"
                reason: str = "NO_RISK_CONFIG"
                quantity: int | None = None
                risk_amount: float | None = None

            logger.warning("FinalAlertGenerator: No RiskConfig found — blocking signal")
            return _DefaultReject()

        # Load daily risk state
        today = date.today()
        rs = await db.execute(
            select(DailyRiskState).where(DailyRiskState.trade_date == today)
        )
        risk_state = rs.scalar_one_or_none()

        # Build portfolio from open trades
        current_balance = float(risk_cfg.paper_balance) if risk_cfg else 100_000.0
        try:
            result_p = await db.execute(
                select(PaperTrade).where(PaperTrade.status == "OPEN")
            )
            open_paper = result_p.scalars().all()
            result_l = await db.execute(
                select(LiveTrade).where(LiveTrade.status == "OPEN")
            )
            open_live = result_l.scalars().all()
            all_open = list(open_paper) + list(open_live)

            portfolio = Portfolio(
                current_balance=current_balance,
                peak_balance=current_balance,
                open_positions=len(all_open),
                open_symbols=[t.symbol for t in all_open],
                open_position_values=[
                    float(t.entry_price or 0) * int(t.quantity or 0)
                    for t in all_open
                ],
                committed_risk=sum(
                    float(getattr(t, "risk_amount", None) or 0)
                    for t in all_open
                ),
            )
        except Exception as e:
            logger.warning(f"FinalAlertGenerator: portfolio load failed: {e}")
            portfolio = Portfolio(
                current_balance=current_balance,
                peak_balance=current_balance,
            )

        # Construct RawSignal for the risk engine
        raw_signal = RawSignal(
            strategy_id=0,
            symbol_id=str(signal.symbol_name or signal.symbol_id),
            signal_type=signal.signal_type,
            entry_price=signal.entry_price,
            stop_loss=signal.stop_loss,
            target_price=signal.target_price,
            atr_value=signal.atr_value,
            candle_time=signal.first_timestamp or datetime.now(IST),
        )

        # Validate through all 17 risk rules
        engine = RiskEngine(risk_cfg)
        decision = engine.validate(raw_signal, risk_state, portfolio)

        return decision


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

        # ── MANDATORY: Risk Engine Validation ─────────────────────────────
        try:
            risk_decision = await _validate_with_risk_engine(signal)
        except Exception as e:
            logger.exception(f"FinalAlertGenerator: Risk Engine error for {sym_name}: {e}")
            await _persist_signal_to_db(
                signal, status="BLOCKED",
                block_reason=f"RISK_ENGINE_ERROR: {str(e)[:200]}",
            )
            SignalTracer.trace_drop(trace_id, "RISK_ENGINE", sym_name, f"Error: {e}")
            return

        if risk_decision.status != "APPROVED":
            # Signal rejected by risk engine — log and persist
            SignalTracer.trace_drop(
                trace_id, "RISK_ENGINE", sym_name,
                f"{risk_decision.status}: {risk_decision.reason}"
            )
            logger.info(
                f"FinalAlertGenerator: {sym_name} {signal.signal_type} "
                f"REJECTED by Risk Engine: {risk_decision.reason}"
            )
            await _persist_signal_to_db(
                signal,
                status=risk_decision.status,
                block_reason=risk_decision.reason,
            )
            return

        # ── Signal APPROVED ───────────────────────────────────────────────
        signal.risk_quantity = risk_decision.quantity
        signal.risk_amount = risk_decision.risk_amount

        SignalTracer.trace_pass(
            trace_id, "RISK_ENGINE", sym_name,
            f"qty={risk_decision.quantity} risk=₹{risk_decision.risk_amount or 0:.0f}"
        )

        logger.info(
            f"🚀 FINAL ALERT: {sym_name} {signal.signal_type} "
            f"@ ₹{signal.entry_price:.2f} → ₹{signal.target_price:.2f} "
            f"(SL ₹{signal.stop_loss:.2f}) "
            f"Score={getattr(signal, 'quality_score', 'N/A')} "
            f"ML={getattr(signal, 'ml_probability', 'N/A')} "
            f"Sentiment={getattr(signal, 'nlp_sentiment', 'N/A')} "
            f"Qty={risk_decision.quantity}"
        )

        # 1. Persist approved signal to DB
        await _persist_signal_to_db(
            signal, status="APPROVED",
            risk_quantity=risk_decision.quantity,
            risk_amount=risk_decision.risk_amount,
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
                "risk_status": "APPROVED",
                "risk_quantity": risk_decision.quantity,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
        except Exception as e:
            logger.debug(f"SSE publish failed for {sym_name}: {e}")

        # 3. Push DIRECTLY to AutoTrader queue (NOT through scanner hook)
        SignalTracer.trace(trace_id, "AUTOTRADER_QUEUE", sym_name, "Enqueueing for execution")
        try:
            from app.engine.auto_trader_engine import autotrader_queue

            # Build a minimal result object for the AutoTrader
            from app.api.routers.scanner import BulkScanResult

            result = BulkScanResult(
                symbol=sym_name,
                ltp=round(float(signal.entry_price), 2),
                change_pct=0.0,
                signal=signal.signal_type,
                entry_price=round(float(signal.entry_price), 2),
                stop_loss=round(float(signal.stop_loss), 2),
                target_price=round(float(signal.target_price), 2),
                risk_reward=round(
                    abs(signal.target_price - signal.entry_price) /
                    max(abs(signal.entry_price - signal.stop_loss), 0.01),
                    2
                ),
                strategy_name=", ".join(getattr(signal, "contributing_strategies", [])),
                rsi=None,
                trend=None,
                ema_cross=None,
                signal_quality_score=getattr(signal, "quality_score", None),
                ml_probability=getattr(signal, "ml_probability", None),
                sentiment=getattr(signal, "nlp_sentiment", None),
                strategies_confirmed=getattr(signal, "contributing_strategies", None),
                data_source="intelligence_pipeline",
                error=None,
            )
            # Enqueue directly to AutoTrader (risk already validated above)
            await autotrader_queue.enqueue([result], "multi_strategy", "5min")

        except Exception as e:
            logger.exception(
                f"FinalAlertGenerator: AutoTrader enqueue failed for {sym_name}: {e}"
            )


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
