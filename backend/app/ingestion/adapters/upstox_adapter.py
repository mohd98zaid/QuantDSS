"""
UpstoxAdapter — Upstox broker integration.
Uses the official upstox-python SDK for Market Data and Execution.
"""

from app.core.config import settings
from app.core.logging import logger
from app.ingestion.broker_adapter import BrokerAdapter


class UpstoxAdapter(BrokerAdapter):
    """Upstox broker adapter."""

    def __init__(self):
        super().__init__(name="upstox")
        self._api = None
        self._feed = None

    async def connect(self) -> bool:
        """Initialize connection using Upstox API credentials."""
        logger.info("UpstoxAdapter: Connecting to Upstox API...")

        if not settings.upstox_api_key or not settings.upstox_api_secret or not settings.upstox_redirect_uri:
            logger.warning("Upstox credentials not fully configured in .env. Skipping connection.")
            return False

        try:
            if not settings.upstox_access_token:
                logger.warning("UpstoxAdapter: Access token missing. Cannot complete connection.")
                self.is_connected = False
                return False

            import httpx
            
            headers = {
                "Accept": "application/json",
                "Authorization": f"Bearer {settings.upstox_access_token}"
            }
            
            async with httpx.AsyncClient() as client:
                response = await client.get("https://api.upstox.com/v2/user/profile", headers=headers)
            
            if response.status_code == 200:
                data = response.json()
                if data.get("status") == "success":
                    self.is_connected = True
                    user_name = data.get("data", {}).get("user_name", "Unknown")
                    logger.info(f"UpstoxAdapter: Connected successfully! Serving user: {user_name}")
                    return True
                    
            logger.error(f"UpstoxAdapter: Connection failed with status {response.status_code}: {response.text}")
            self.is_connected = False
            return False
                
        except Exception as e:
            logger.error(f"UpstoxAdapter: Exception during connection: {e}")
            self.is_connected = False
            return False

    async def disconnect(self) -> None:
        """Close connection."""
        self.is_connected = False
        logger.info("UpstoxAdapter: Disconnected")

    async def subscribe(self, symbols: list[str]) -> None:
        """Subscribe to live tick data via Upstox Market Data Feed."""
        logger.info(f"UpstoxAdapter: Subscribe stub for {symbols}")

    async def unsubscribe(self, symbols: list[str]) -> None:
        """Unsubscribe from tick data."""
        logger.info(f"UpstoxAdapter: Unsubscribe stub for {symbols}")

    async def get_connection_status(self) -> dict:
        return {
            "adapter": self.name,
            "status": "CONNECTED" if self.is_connected else "DISCONNECTED",
            "subscribed_symbols": [],
            "last_tick_at": None,
        }
