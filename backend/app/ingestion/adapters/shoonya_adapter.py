"""
ShoonyaAdapter — Shoonya (Finvasia) broker integration.
PRIMARY broker — Free WebSocket + REST API.
Full implementation in Week 2.
"""

from app.core.config import settings
from app.core.logging import logger
from app.ingestion.broker_adapter import BrokerAdapter


class ShoonyaAdapter(BrokerAdapter):
    """Shoonya (Finvasia) broker adapter — primary, free API."""

    def __init__(self):
        super().__init__(name="shoonya")
        self._api = None
        self._ws = None

    async def connect(self) -> bool:
        """Login with user/password/TOTP and establish WebSocket."""
        logger.info("ShoonyaAdapter: Connecting to Shoonya API...")

        if not settings.shoonya_user_id or not settings.shoonya_api_key:
            logger.warning("Shoonya credentials not configured. Skipping connection.")
            return False

        try:
            # NorenRestApiPy integration will be implemented in Week 2
            # from NorenRestApiPy import NorenApi
            # self._api = NorenApi(...)
            # self._api.set_session(...)
            logger.info("ShoonyaAdapter: Connection stub — full implementation in Week 2")
            self.is_connected = False
            return False
        except Exception as e:
            logger.error(f"ShoonyaAdapter: Connection failed: {e}")
            return False

    async def disconnect(self) -> None:
        """Close WebSocket connection."""
        self.is_connected = False
        logger.info("ShoonyaAdapter: Disconnected")

    async def subscribe(self, symbols: list[str]) -> None:
        """Subscribe to live tick data."""
        logger.info(f"ShoonyaAdapter: Subscribe stub for {symbols}")

    async def unsubscribe(self, symbols: list[str]) -> None:
        """Unsubscribe from tick data."""
        logger.info(f"ShoonyaAdapter: Unsubscribe stub for {symbols}")

    async def get_connection_status(self) -> dict:
        return {
            "adapter": self.name,
            "status": "CONNECTED" if self.is_connected else "DISCONNECTED",
            "subscribed_symbols": [],
            "last_tick_at": None,
        }
