"""
TelegramNotifier — Send signal alerts, halt alerts, and EOD summaries via Telegram Bot.
"""

from app.core.config import settings
from app.core.logging import logger
from app.engine.base_strategy import RawSignal
from app.engine.risk_engine import RiskDecision


class TelegramNotifier:
    """Sends formatted trading alerts to Telegram."""

    def __init__(self):
        self.bot_token = settings.telegram_bot_token
        self.chat_id = settings.telegram_chat_id
        self._bot = None

    async def _get_bot(self):
        """Lazy-initialize bot."""
        if self._bot is None and self.bot_token:
            try:
                from telegram import Bot
                self._bot = Bot(token=self.bot_token)
            except Exception as e:
                logger.warning(f"Telegram bot init failed: {e}")
        return self._bot

    @property
    def is_configured(self) -> bool:
        return bool(self.bot_token and self.chat_id)

    async def send_message(self, text: str) -> bool:
        """Send a message to the configured Telegram chat."""
        if not self.is_configured:
            logger.debug("Telegram not configured — skipping notification")
            return False

        try:
            bot = await self._get_bot()
            if bot:
                for attempt in range(3):
                    try:
                        await bot.send_message(
                            chat_id=self.chat_id,
                            text=text,
                            parse_mode="HTML",
                        )
                        return True
                    except Exception as e:
                        logger.error(f"Telegram send failed attempt {attempt+1}: {e}")
                        import asyncio
                        await asyncio.sleep(1)
        except Exception as e:
            logger.error(f"Telegram bot initialization failed: {e}")
        return False

    async def send_signal_alert(
        self,
        signal: RawSignal,
        decision: RiskDecision,
        symbol_name: str,
        strategy_name: str,
    ) -> bool:
        """Send a formatted signal alert."""
        if decision.status == "APPROVED":
            emoji = "🟢"
            status_text = "APPROVED"
        elif decision.status == "BLOCKED":
            emoji = "🔴"
            status_text = f"BLOCKED: {decision.reason}"
        else:
            emoji = "🟡"
            status_text = f"SKIPPED: {decision.reason}"

        direction_emoji = "📈" if signal.signal_type == "BUY" else "📉"

        text = (
            f"{emoji} <b>Signal {status_text}</b>\n"
            f"\n"
            f"{direction_emoji} <b>{signal.signal_type}</b> {symbol_name}\n"
            f"Strategy: {strategy_name}\n"
            f"\n"
            f"Entry: ₹{signal.entry_price:.2f}\n"
            f"Stop Loss: ₹{signal.stop_loss:.2f}\n"
            f"Target: ₹{signal.target_price:.2f}\n"
            f"R:R: {signal.risk_reward:.1f}\n"
        )

        if decision.status == "APPROVED" and decision.quantity:
            text += (
                f"\n"
                f"Qty: {decision.quantity}\n"
                f"Risk: ₹{decision.risk_amount:.0f} ({decision.risk_pct:.2f}%)\n"
            )

        return await self.send_message(text)

    async def send_halt_alert(self, reason: str, pnl: float = 0) -> bool:
        """Send a trading halt alert."""
        text = (
            f"🚨 <b>TRADING HALTED</b>\n"
            f"\n"
            f"Reason: {reason}\n"
            f"Today's P&L: ₹{pnl:.2f}\n"
            f"\n"
            f"All signals will be blocked until reset."
        )
        return await self.send_message(text)

    async def send_eod_summary(
        self,
        date: str,
        total_signals: int,
        approved: int,
        blocked: int,
        skipped: int,
        trades_taken: int,
        net_pnl: float,
    ) -> bool:
        """Send end-of-day trading summary."""
        pnl_emoji = "💰" if net_pnl >= 0 else "📉"

        text = (
            f"📊 <b>EOD Summary — {date}</b>\n"
            f"\n"
            f"Signals: {total_signals} "
            f"(✅{approved} 🚫{blocked} ⏭{skipped})\n"
            f"Trades Taken: {trades_taken}\n"
            f"{pnl_emoji} Net P&L: ₹{net_pnl:.2f}\n"
        )
        return await self.send_message(text)

telegram_notifier_instance = TelegramNotifier()

def send_telegram_alert(message: str):
    """
    Fire and forget helper to send a telegram alert asynchronously.
    """
    import asyncio
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(telegram_notifier_instance.send_message(message))
    except RuntimeError:
        # If no running loop, we can't easily fire-and-forget without creating one.
        pass
