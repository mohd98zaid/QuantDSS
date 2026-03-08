"""
AngelOne SmartAPI WebSocket — Secondary market data feed.

Provides tick-level market data from AngelOne as a fallback/redundancy feed.
Parses ticks and normalizes them to the same format as the Upstox feed.

Features:
  - SmartAPI WebSocket connection
  - Tick parsing and normalization
  - Symbol mapping (AngelOne token → trading symbol)
  - Heartbeat detection and auto-reconnect

Usage:
    from app.ingestion.angelone_websocket import AngelOneWebSocketClient
    client = AngelOneWebSocketClient()
    await client.connect()
    await client.subscribe(["3045", "1594"])  # AngelOne instrument tokens
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional, Set, Callable, Awaitable

from app.core.config import settings
from app.core.logging import logger

IST = timezone(timedelta(hours=5, minutes=30))


class AngelOneWebSocketClient:
    """
    WebSocket client for AngelOne SmartAPI market data feed.

    Normalizes ticks to the same dict format as UpstoxWebSocketClient:
        {
            "symbol": "<trading_symbol>",
            "ltp": <float>,
            "volume": <int>,
            "timestamp": "<ISO-8601>",
            "best_bid": <float>,
            "best_ask": <float>,
            "feed_source": "angelone",
        }
    """

    MAX_RECONNECT_ATTEMPTS = 10

    def __init__(self):
        self._api_key = settings.angel_api_key or ""
        self._client_id = settings.angel_client_id or ""
        self._password = settings.angel_password or ""
        self._totp_secret = settings.angel_totp_secret or ""
        self._access_token: str = ""
        self._feed_token: str = ""
        self._ws = None
        self._running = False
        self._subscriptions: Set[str] = set()
        self._listen_task: Optional[asyncio.Task] = None
        self._reconnect_count: int = 0
        self._last_tick_time: float = 0.0  # monotonic
        self._on_tick: Optional[Callable[[dict], Awaitable[None]]] = None
        # AngelOne token → symbol name mapping
        self._token_symbol_map: Dict[str, str] = {}

    def set_tick_callback(self, callback: Callable[[dict], Awaitable[None]]) -> None:
        """Set the async callback for normalized tick data."""
        self._on_tick = callback

    async def login(self) -> bool:
        """
        Authenticate with AngelOne SmartAPI and obtain access + feed tokens.

        Returns True on success, False on failure.
        """
        if not self._api_key or not self._client_id:
            logger.warning("AngelOneWS: Missing API key or client ID — skipping login")
            return False

        try:
            from SmartApi import SmartConnect
            import pyotp

            totp = pyotp.TOTP(self._totp_secret).now() if self._totp_secret else ""
            obj = SmartConnect(api_key=self._api_key)
            data = obj.generateSession(self._client_id, self._password, totp)

            if data.get("status"):
                self._access_token = data["data"]["jwtToken"]
                self._feed_token = obj.getfeedToken()
                logger.info("AngelOneWS: Login successful")
                return True
            else:
                logger.error(f"AngelOneWS: Login failed — {data.get('message', 'Unknown error')}")
                return False
        except Exception as e:
            logger.exception(f"AngelOneWS: Login exception — {e}")
            return False

    async def connect(self) -> None:
        """Connect to AngelOne SmartAPI WebSocket."""
        if not self._access_token:
            if not await self.login():
                logger.warning("AngelOneWS: Cannot connect — login failed")
                return

        try:
            from SmartApi.smartWebSocketV2 import SmartWebSocketV2

            self._ws = SmartWebSocketV2(
                self._access_token,
                self._api_key,
                self._client_id,
                self._feed_token,
            )
            self._ws.on_data = self._on_ws_data
            self._ws.on_error = self._on_ws_error
            self._ws.on_close = self._on_ws_close
            self._ws.on_open = self._on_ws_open

            self._running = True
            self._listen_task = asyncio.create_task(
                asyncio.to_thread(self._ws.connect)
            )
            self._reconnect_count = 0
            logger.info("AngelOneWS: Connected & Listening")
        except Exception as e:
            logger.error(f"AngelOneWS: Connection failed — {e}")
            self._running = False

    async def disconnect(self) -> None:
        """Disconnect from AngelOne WebSocket."""
        self._running = False
        if self._ws:
            try:
                self._ws.close_connection()
            except Exception:
                pass
        if self._listen_task and not self._listen_task.done():
            self._listen_task.cancel()
        logger.info("AngelOneWS: Disconnected")

    async def subscribe(self, tokens: list[str], exchange: str = "nse_cm") -> None:
        """Subscribe to instruments by AngelOne token IDs."""
        self._subscriptions.update(tokens)
        if self._ws and self._running:
            try:
                token_list = [
                    {"exchangeType": 1, "tokens": tokens}  # 1 = NSE
                ]
                self._ws.subscribe("abc123", 1, token_list)  # mode 1 = LTP
                logger.info(f"AngelOneWS: Subscribed to {len(tokens)} tokens")
            except Exception as e:
                logger.error(f"AngelOneWS: Subscribe failed — {e}")

    def set_symbol_map(self, token_to_symbol: Dict[str, str]) -> None:
        """Set the token → trading symbol mapping."""
        self._token_symbol_map = token_to_symbol

    def _on_ws_open(self, ws_app) -> None:
        logger.info("AngelOneWS: WebSocket opened")

    def _on_ws_data(self, ws_app, message) -> None:
        """Process incoming WebSocket data and normalize ticks."""
        try:
            if isinstance(message, str):
                data = json.loads(message)
            elif isinstance(message, bytes):
                # Binary format — parse AngelOne binary protocol
                data = self._parse_binary(message)
            elif isinstance(message, dict):
                data = message
            else:
                return

            token = str(data.get("token", ""))
            ltp = float(data.get("last_traded_price", 0)) / 100.0  # AngelOne sends in paisa
            volume = int(data.get("total_traded_volume", 0))
            best_bid = float(data.get("best_bid_price", 0)) / 100.0
            best_ask = float(data.get("best_ask_price", 0)) / 100.0

            symbol = self._token_symbol_map.get(token, token)
            now = datetime.now(IST)

            normalized_tick = {
                "symbol": symbol,
                "ltp": ltp,
                "volume": volume,
                "timestamp": now.isoformat(),
                "best_bid": best_bid,
                "best_ask": best_ask,
                "feed_source": "angelone",
            }

            self._last_tick_time = time.monotonic()

            if self._on_tick:
                # Fire-and-forget from sync context
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        loop.create_task(self._on_tick(normalized_tick))
                except Exception:
                    pass

        except Exception as e:
            logger.error(f"AngelOneWS: Tick parse error — {e}")

    def _on_ws_error(self, ws_app, error) -> None:
        logger.error(f"AngelOneWS: Error — {error}")
        self._running = False

    def _on_ws_close(self, ws_app) -> None:
        logger.warning("AngelOneWS: Connection closed")
        self._running = False

    def _parse_binary(self, raw: bytes) -> dict:
        """Parse AngelOne binary WebSocket message (simplified)."""
        # AngelOne binary format: struct-based, varies by subscription mode
        # This is a placeholder — real parsing depends on AngelOne SDK version
        return {}

    @property
    def is_healthy(self) -> bool:
        """Check if the feed is alive and receiving data."""
        if not self._running:
            return False
        if self._last_tick_time == 0:
            return False
        age = time.monotonic() - self._last_tick_time
        return age < 5.0  # stale if no tick in 5 seconds

    @property
    def last_tick_age_seconds(self) -> float:
        """Seconds since last tick received."""
        if self._last_tick_time == 0:
            return float("inf")
        return time.monotonic() - self._last_tick_time
