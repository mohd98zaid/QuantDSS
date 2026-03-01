"""
AlertDispatcher — Coordinates Telegram + SSE notifications.
Single entry point for all alert dispatch after risk engine decision.
"""
from app.alerts.sse_manager import SSEManager
from app.alerts.telegram_notifier import TelegramNotifier
from app.core.logging import logger
from app.engine.base_strategy import RawSignal
from app.engine.risk_engine import RiskDecision


class AlertDispatcher:
    """
    Dispatches alerts to all configured channels.
    Called after every Risk Engine decision.
    """

    def __init__(self):
        self.telegram = TelegramNotifier()

    async def dispatch_signal(
        self,
        signal: RawSignal,
        decision: RiskDecision,
        symbol_name: str = "",
        strategy_name: str = "",
    ) -> None:
        """
        Dispatch a signal notification via all channels.
        Called for APPROVED, BLOCKED, and SKIPPED signals.
        """
        # 1. SSE push to dashboard (always)
        try:
            await SSEManager.publish_signal_event({
                "signal_type": signal.signal_type,
                "symbol": symbol_name,
                "strategy": strategy_name,
                "entry_price": signal.entry_price,
                "stop_loss": signal.stop_loss,
                "target_price": signal.target_price,
                "risk_status": decision.status,
                "block_reason": decision.reason,
                "quantity": decision.quantity,
                "risk_amount": decision.risk_amount,
                "risk_reward": decision.risk_reward,
            })
        except Exception as e:
            logger.error(f"SSE dispatch failed: {e}")

        # 2. Telegram (for APPROVED and BLOCKED only — skip SKIPPED to avoid spam)
        if decision.status in ("APPROVED", "BLOCKED"):
            try:
                await self.telegram.send_signal_alert(
                    signal, decision, symbol_name, strategy_name
                )
            except Exception as e:
                logger.error(f"Telegram dispatch failed: {e}")

    async def dispatch_halt(self, reason: str, pnl: float = 0) -> None:
        """Dispatch a trading halt notification."""
        try:
            await SSEManager.publish_halt_event(reason)
        except Exception as e:
            logger.error(f"SSE halt dispatch failed: {e}")

        try:
            await self.telegram.send_halt_alert(reason, pnl)
        except Exception as e:
            logger.error(f"Telegram halt dispatch failed: {e}")

    async def dispatch_eod_summary(
        self,
        date: str,
        total_signals: int,
        approved: int,
        blocked: int,
        skipped: int,
        trades_taken: int,
        net_pnl: float,
    ) -> None:
        """Dispatch end-of-day summary."""
        try:
            await self.telegram.send_eod_summary(
                date, total_signals, approved, blocked, skipped, trades_taken, net_pnl
            )
        except Exception as e:
            logger.error(f"EOD summary dispatch failed: {e}")
