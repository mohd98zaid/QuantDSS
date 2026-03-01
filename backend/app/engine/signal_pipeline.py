"""
SignalPipeline — End-to-end orchestrator: Candle → Strategy → Risk → Alert → DB.
This is the main pipeline that runs on every candle close.
"""
from datetime import UTC, datetime

import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.alert_dispatcher import AlertDispatcher
from app.core.logging import logger
from app.engine.base_strategy import RawSignal
from app.engine.risk_engine import Portfolio, RiskDecision, RiskEngine
from app.engine.strategy_runner import StrategyRunner
from app.models.audit_log import AuditLog
from app.models.daily_risk_state import DailyRiskState
from app.models.signal import Signal as SignalModel


class SignalPipeline:
    """
    End-to-end signal pipeline.

    Flow:
    1. Receive completed 1-min candle
    2. Fetch lookback candles from DB
    3. Run StrategyRunner.evaluate() → list of RawSignals
    4. For each signal: RiskEngine.validate() → RiskDecision
    5. Log decision to DB (signals table + audit_log)
    6. Dispatch alerts (SSE + Telegram)
    """

    def __init__(
        self,
        strategy_runner: StrategyRunner,
        risk_engine: RiskEngine,
        alert_dispatcher: AlertDispatcher,
    ):
        self.strategy_runner = strategy_runner
        self.risk_engine = risk_engine
        self.alert_dispatcher = alert_dispatcher

    async def process_candle(
        self,
        candles: pd.DataFrame,
        symbol_id: int,
        symbol_name: str,
        state: DailyRiskState,
        portfolio: Portfolio,
        db: AsyncSession,
    ) -> list[RiskDecision]:
        """
        Process a completed candle through the full pipeline.

        Args:
            candles: DataFrame with recent OHLCV history for the symbol
            symbol_id: Database ID of the symbol
            symbol_name: Trading symbol name (e.g., "RELIANCE")
            state: Today's daily risk state
            portfolio: Current portfolio state
            db: Database session for logging

        Returns:
            List of RiskDecision objects for each signal generated
        """
        decisions = []

        # 1. Run strategy evaluation
        raw_signals = self.strategy_runner.evaluate(candles, symbol_id)

        if not raw_signals:
            return decisions

        logger.info(f"Pipeline: {len(raw_signals)} raw signals for {symbol_name}")

        # 2. Process each signal through risk engine
        for signal in raw_signals:
            try:
                # Get strategy name
                strategy_name = self._get_strategy_name(signal.strategy_id)

                # Risk validation
                decision = self.risk_engine.validate(signal, state, portfolio)
                decisions.append(decision)

                # 3. Log to database
                await self._log_signal(db, signal, decision)

                # 4. Dispatch alerts
                await self.alert_dispatcher.dispatch_signal(
                    signal, decision, symbol_name, strategy_name
                )

                # 5. Update daily risk state
                await self._update_risk_state(db, state, decision)

                # 6. If BLOCKED by daily loss, trigger halt
                if decision.reason in ("DAILY_LOSS_LIMIT_REACHED", "ACCOUNT_DRAWDOWN_HALT"):
                    state.is_halted = True
                    state.halt_reason = decision.reason
                    state.halt_triggered_at = datetime.now(UTC)
                    await self.alert_dispatcher.dispatch_halt(
                        decision.reason,
                        float(state.realised_pnl or 0),
                    )

            except Exception as e:
                logger.error(f"Pipeline error for signal {signal.signal_type}: {e}")

        return decisions

    async def _log_signal(
        self,
        db: AsyncSession,
        signal: RawSignal,
        decision: RiskDecision,
    ) -> None:
        """Insert signal record into the signals table."""
        atr_pct = (signal.atr_value / signal.entry_price) if signal.entry_price > 0 else 0

        db_signal = SignalModel(
            strategy_id=signal.strategy_id,
            symbol_id=signal.symbol_id,
            signal_type=signal.signal_type,
            entry_price=signal.entry_price,
            stop_loss=signal.stop_loss,
            target_price=signal.target_price,
            quantity=decision.quantity,
            risk_amount=decision.risk_amount,
            risk_pct=decision.risk_pct,
            risk_reward=decision.risk_reward,
            risk_status=decision.status,
            block_reason=decision.reason,
            atr_value=signal.atr_value,
            atr_pct=atr_pct,
            candle_time=signal.candle_time,
        )
        db.add(db_signal)

        # Also log to audit trail
        audit = AuditLog(
            event_type=f"SIGNAL_{decision.status}",
            entity_type="signals",
            payload={
                "signal_type": signal.signal_type,
                "symbol_id": signal.symbol_id,
                "entry_price": float(signal.entry_price),
                "risk_status": decision.status,
                "reason": decision.reason,
            },
        )
        db.add(audit)

        await db.flush()

    async def _update_risk_state(
        self,
        db: AsyncSession,
        state: DailyRiskState,
        decision: RiskDecision,
    ) -> None:
        """Update daily risk state counters."""
        if decision.status == "APPROVED":
            state.signals_approved = (state.signals_approved or 0) + 1
            state.last_signal_time = datetime.now(UTC)
        elif decision.status == "BLOCKED":
            state.signals_blocked = (state.signals_blocked or 0) + 1
        elif decision.status == "SKIPPED":
            state.signals_skipped = (state.signals_skipped or 0) + 1

        await db.flush()

    def _get_strategy_name(self, strategy_id: int) -> str:
        """Look up strategy name from loaded strategies."""
        if strategy_id in self.strategy_runner._strategies:
            strategy = self.strategy_runner._strategies[strategy_id]
            return strategy.__class__.__name__
        return f"Strategy-{strategy_id}"
