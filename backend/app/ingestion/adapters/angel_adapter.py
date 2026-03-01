"""
AngelOneAdapter — Angel One SmartAPI integration.
FALLBACK broker — Free API.
Full implementation in Week 2.
"""

from app.core.config import settings
from app.core.logging import logger
from app.ingestion.broker_adapter import BrokerAdapter


class AngelOneAdapter(BrokerAdapter):
    """Angel One SmartAPI broker adapter — fallback, free API."""

    def __init__(self):
        super().__init__(name="angel_one")

    async def connect(self) -> bool:
        try:
            if not all([settings.angel_api_key, settings.angel_client_id, settings.angel_password, settings.angel_totp_secret]):
                logger.warning("AngelOneAdapter: Missing credentials. Cannot connect.")
                self.is_connected = False
                return False

            from SmartApi import SmartConnect
            import pyotp
            
            self._api = SmartConnect(api_key=settings.angel_api_key)
            totp = pyotp.TOTP(settings.angel_totp_secret).now()
            
            # Using synchronous call in a thread or awaiting if SDK supports async?
            # SmartAPI generateSession is synchronous
            data = self._api.generateSession(
                settings.angel_client_id,
                settings.angel_password,
                totp
            )
            
            if data and data.get("status"):
                self.is_connected = True
                logger.info(f"AngelOneAdapter: Connected successfully as {settings.angel_client_id}")
                return True
            else:
                error_msg = data.get("message", "Unknown error") if data else "Empty response"
                logger.error(f"AngelOneAdapter: Connection failed: {error_msg}")
                self.is_connected = False
                return False
                
        except Exception as e:
            logger.error(f"AngelOneAdapter: Exception during connection: {e}")
            self.is_connected = False
            return False

    async def disconnect(self) -> None:
        self.is_connected = False

    async def subscribe(self, symbols: list[str]) -> None:
        logger.info(f"AngelOneAdapter: Subscribe stub for {symbols}")

    async def unsubscribe(self, symbols: list[str]) -> None:
        logger.info(f"AngelOneAdapter: Unsubscribe stub for {symbols}")

    async def get_connection_status(self) -> dict:
        return {
            "adapter": self.name,
            "status": "CONNECTED" if self.is_connected else "DISCONNECTED",
            "subscribed_symbols": [],
            "last_tick_at": None,
        }
