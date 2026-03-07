"""
Notifier - Pushes critical trading system alerts to external webhooks (e.g. Discord/Telegram)
"""
import httpx
from typing import Optional
from app.core.logging import logger
from app.core.config import settings

class Notifier:
    """Handles external alerting for critical system events."""

    def __init__(self):
        # We expect a generic webhook URL (like Discord) or a Telegram API URL + Chat ID
        # In this implementation we will assume a generic HTTP Post webhook (Discord style)
        self.webhook_url = getattr(settings, "alert_webhook_url", None)

    async def send_alert(self, title: str, message: str, level: str = "INFO"):
        """
        Send a markdown formatted alert to the external webhook.
        Level options: INFO, WARNING, CRITICAL, SUCCESS
        """
        if not self.webhook_url:
            logger.debug(f"Notifier: Webhook URL not configured. Skipped alert: {title}")
            return

        colors = {
            "INFO": 3447003,       # Blue
            "WARNING": 16776960,   # Yellow
            "CRITICAL": 15158332,  # Red
            "SUCCESS": 3066993     # Green
        }
        
        color = colors.get(level.upper(), 3447003)

        payload = {
            "embeds": [
                {
                    "title": f"[{level.upper()}] {title}",
                    "description": message,
                    "color": color
                }
            ]
        }

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.post(self.webhook_url, json=payload)
                if response.status_code >= 400:
                    logger.error(f"Failed to send webhook alert {response.status_code}: {response.text}")
                else:
                    logger.debug(f"Successfully sent alert: {title}")
        except Exception as e:
            logger.error(f"Notifier exception when sending alert: {e}")

# Global instance
notifier = Notifier()
