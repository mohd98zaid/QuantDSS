"""
Risk Engine Worker — Standalone service (Phase 5).

Consumes approved signals from `signals:approved`, runs the full RiskEngine
rule chain, and publishes passed signals to `signals:risk_passed`.

Run:
    python -m app.workers.risk_engine_worker
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone, timedelta, date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_session_factory
from app.core.logging import logger
from app.core.streams import (
    STREAM_SIGNALS_APPROVED,
    STREAM_SIGNALS_RISK_PASSED,
    consume_stream,
    publish_to_stream,
)
from app.engine.base_strategy import RawSignal
from app.engine.risk_engine import RiskEngine, Portfolio, RiskDecision
from app.models.daily_risk_state import DailyRiskState
from app.models.risk_config import RiskConfig
from app.models.signal import Signal as SignalModel
from app.workers.base import WorkerBase


IST = timezone(timedelta(hours=5, minutes=30))


class RiskEngineWorker(WorkerBase):
    """
    Consumes approved signals from Redis, validates them through the full
    RiskEngine rule chain, and publishes passed signals downstream.
    """

    NAME = "risk-engine-worker"
    CONSUMER_GROUP = "risk_engine_group"
    CONSUMER_NAME = "risk_engine_1"

    def __init__(self):
        super().__init__()
        self._risk_engine: RiskEngine | None = None
        self._risk_config = None

    # ── Initialization ───────────────────────────────────────────────────────

    async def _load_risk_config(self) -> None:
        """Load risk configuration from DB and initialize RiskEngine."""
        async with async_session_factory() as db:
            result = await db.execute(select(RiskConfig).limit(1))
            config = result.scalar_one_or_none()

            if config is None:
                logger.warning(f"[{self.NAME}] No RiskConfig found — using defaults")
                from app.core.config import settings
                config = type("DefaultConfig", (), {
                    "risk_per_trade_pct": settings.risk_per_trade_pct,
                    "max_daily_loss_inr": settings.max_daily_loss_inr,
                    "max_daily_loss_pct": settings.max_daily_loss_pct,
                    "max_account_drawdown_pct": settings.max_account_drawdown_pct,
                    "cooldown_minutes": settings.cooldown_minutes,
                    "min_atr_pct": settings.min_atr_pct,
                    "max_atr_pct": settings.max_atr_pct,
                    "max_position_pct": settings.max_position_pct,
                    "max_concurrent_positions": settings.max_concurrent_positions,
                    "signal_start_hour": 9,
                    "signal_start_minute": 20,
                    "signal_end_hour": 14,
                    "signal_end_minute": 30,
                    "max_signals_per_stock": 3,
                    "max_weekly_loss_inr": 2000.0,
                    "max_weekly_loss_pct": 0.05,
                    "min_risk_reward": 1.5,
                    "max_consecutive_errors": 5,
                    "max_correlated_positions": 3,
                    "min_daily_volume": 500000,
                    "max_spread_pct": 0.005,
                    "market_regime": "NONE",
                    "paper_balance": 100000.0,
                })()

            self._risk_config = config
            self._risk_engine = RiskEngine(config)
            logger.info(f"[{self.NAME}] RiskEngine initialized with DB config")

    async def _load_portfolio(self, db: AsyncSession) -> Portfolio:
        """Build Portfolio from current DB state."""
        try:
            from app.engine.auto_trader_engine import _load_portfolio
            return await _load_portfolio(db, self._risk_config)
        except Exception as e:
            logger.warning(f"[{self.NAME}] Portfolio load fallback: {e}")
            paper_balance = getattr(self._risk_config, "paper_balance", 100000.0) or 100000.0
            return Portfolio(
                current_balance=float(paper_balance),
                peak_balance=float(paper_balance),
            )

    async def _load_daily_state(self, db: AsyncSession) -> DailyRiskState:
        """Load or create today's DailyRiskState."""
        today = datetime.now(IST).date()
        result = await db.execute(
            select(DailyRiskState).where(DailyRiskState.date == today).limit(1)
        )
        state = result.scalar_one_or_none()
        if state is None:
            state = DailyRiskState(date=today)
            db.add(state)
            await db.commit()
            await db.refresh(state)
        return state

    # ── Signal Handler ───────────────────────────────────────────────────────

    async def _handle_signal(self, msg_id: str, data: dict[str, str]):
        """Process an approved signal through the risk engine."""
        symbol_id = int(data.get("symbol_id", "0"))
        symbol_name = data.get("symbol_name", "")
        signal_type = data.get("signal_type", "")
        entry_price = float(data.get("entry_price", "0"))
        stop_loss = float(data.get("stop_loss", "0"))
        target_price = float(data.get("target_price", "0"))
        atr_value = float(data.get("atr_value", "0"))
        candle_time_str = data.get("candle_time", "")
        contributing_strategies = data.get("contributing_strategies", "[]")
        quality_score = data.get("quality_score", "0")

        candle_time = (
            datetime.fromisoformat(candle_time_str) if candle_time_str
            else datetime.now(timezone.utc)
        )

        # Reconstruct a RawSignal for the risk engine
        raw_signal = RawSignal(
            symbol_id=symbol_id,
            strategy_id=0,
            strategy_name=contributing_strategies,
            signal_type=signal_type,
            entry_price=entry_price,
            stop_loss=stop_loss,
            target_price=target_price,
            atr_value=atr_value,
            candle_time=candle_time,
            symbol_name=symbol_name,
        )

        async with async_session_factory() as db:
            portfolio = await self._load_portfolio(db)
            state = await self._load_daily_state(db)

            # Run risk validation
            decision: RiskDecision = self._risk_engine.validate(
                raw_signal, state, portfolio,
            )

            if decision.status == "APPROVED":
                # Publish to signals:risk_passed
                message = {
                    **data,  # Pass through all original fields
                    "quantity": str(decision.quantity or 0),
                    "risk_amount": str(decision.risk_amount or 0),
                    "risk_pct": str(decision.risk_pct or 0),
                    "risk_reward": str(decision.risk_reward or 0),
                    "risk_status": "APPROVED",
                }
                pub_id = await publish_to_stream(STREAM_SIGNALS_RISK_PASSED, message)

                # Update daily state counters
                state.signals_approved = (state.signals_approved or 0) + 1
                state.last_signal_time = datetime.now(timezone.utc)
                await db.commit()

                logger.info(
                    f"[{self.NAME}] ✅ Risk APPROVED: {symbol_name} {signal_type} "
                    f"qty={decision.quantity} → {STREAM_SIGNALS_RISK_PASSED} (id={pub_id})"
                )
            else:
                # Log rejection
                status_label = decision.status  # BLOCKED or SKIPPED
                reason = decision.reason or "unknown"

                if status_label == "BLOCKED":
                    state.signals_blocked = (state.signals_blocked or 0) + 1
                else:
                    state.signals_skipped = (state.signals_skipped or 0) + 1
                await db.commit()

                # Persist rejected signal to DB
                try:
                    db.add(SignalModel(
                        symbol_id=symbol_id if symbol_id else None,
                        signal_type=signal_type,
                        entry_price=entry_price,
                        stop_loss=stop_loss,
                        target_price=target_price,
                        risk_status=status_label,
                        block_reason=f"RiskEngine: {reason}",
                        confidence_score=int(float(quality_score) if quality_score else 0),
                        candle_time=candle_time,
                    ))
                    await db.commit()
                except Exception as e:
                    logger.warning(f"[{self.NAME}] Rejected signal DB log failed: {e}")

                logger.info(
                    f"[{self.NAME}] ❌ Risk {status_label}: {symbol_name} {signal_type} "
                    f"— {reason}"
                )

    # ── Main Loop ────────────────────────────────────────────────────────────

    async def run(self):
        """Main worker loop — consume approved signals and validate risk."""
        await self._load_risk_config()

        # Wait briefly for infrastructure
        await asyncio.sleep(1)

        await consume_stream(
            stream=STREAM_SIGNALS_APPROVED,
            group=self.CONSUMER_GROUP,
            consumer=self.CONSUMER_NAME,
            handler=self._handle_signal,
            running=lambda: self.is_running,
        )


# ── Entry Point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    RiskEngineWorker().main()
