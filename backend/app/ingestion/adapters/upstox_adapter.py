"""
UpstoxAdapter — Upstox broker integration.

Token hot-reload:  connect() always calls _fresh_token() which checks
three sources in priority order:
  1. In-memory override  (set via PATCH /api/v1/health/broker/token — no restart)
  2. .env file on disk   (backend/  mounted at /app in Docker, .env is one level up)
  3. OS environment var  (Docker Compose injects from host .env at container start)
"""
import os
from pathlib import Path

import httpx
from dotenv import dotenv_values

from app.core.config import settings
from app.core.logging import logger
from app.ingestion.broker_adapter import BrokerAdapter

# ── Paths to .env file (works both locally and inside Docker)
#    In Docker: backend/ is mounted as /app, so .env is at /app/../.env  
#    Locally:   backend/ is at <project>/backend, so .env is at <project>/.env
_ENV_PATH = Path(__file__).resolve().parents[4] / ".env"          # local path
_ENV_PATH_DOCKER = Path("/app").parent / ".env"                   # Docker path

# ── In-memory override — set this via PATCH /api/v1/health/broker/token
#    Takes priority over .env file and env var.  Cleared on container restart.
_token_override: str = ""


def set_token_override(token: str) -> None:
    """Set the token in-memory so recovery picks it up in < 60 s."""
    global _token_override
    _token_override = token.strip()


def _fresh_token() -> str:
    """Return the best available Upstox access token (3-source priority)."""
    global _token_override
    # 1. In-memory override (updated via API endpoint — no restart needed)
    if _token_override:
        return _token_override
    # 2. .env file on disk — check both possible locations
    for dotenv_path in (_ENV_PATH_DOCKER, _ENV_PATH):
        if dotenv_path.exists():
            val = dotenv_values(str(dotenv_path)).get("UPSTOX_ACCESS_TOKEN", "")
            if val:
                return val.strip()
    # 3. Fallback: OS env var (set by Docker Compose at container start)
    return os.environ.get("UPSTOX_ACCESS_TOKEN", "").strip()


class UpstoxAdapter(BrokerAdapter):
    """Upstox broker adapter."""

    def __init__(self):
        super().__init__(name="upstox")

    async def connect(self) -> bool:
        """
        Attempt to connect using Upstox API credentials.

        The access token is re-read from .env on every call so that
        updating the token file is sufficient — no restart required.
        """
        logger.info("UpstoxAdapter: Connecting to Upstox API...")

        if not settings.upstox_api_key or not settings.upstox_api_secret:
            logger.warning("UpstoxAdapter: API key/secret not configured. Skipping.")
            return False

        # ── Hot-reload token from .env on disk ──────────────────────────────
        token = _fresh_token()
        if not token:
            logger.warning(
                "UpstoxAdapter: UPSTOX_ACCESS_TOKEN is empty in .env — "
                "update the token and the recovery task will retry in 60 s."
            )
            self.is_connected = False
            return False

        try:
            headers = {
                "Accept": "application/json",
                "Authorization": f"Bearer {token}",
            }
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(
                    "https://api.upstox.com/v2/user/profile",
                    headers=headers,
                )

            if response.status_code == 200:
                data = response.json()
                if data.get("status") == "success":
                    self.is_connected = True
                    user_name = data.get("data", {}).get("user_name", "Unknown")
                    logger.info(f"UpstoxAdapter: Connected ✓ — user: {user_name}")
                    return True

            logger.error(
                f"UpstoxAdapter: HTTP {response.status_code} — {response.text[:200]}"
            )
            self.is_connected = False
            return False

        except Exception as exc:
            logger.error(f"UpstoxAdapter: Exception during connect: {exc}")
            self.is_connected = False
            return False

    async def disconnect(self) -> None:
        """Close connection."""
        self.is_connected = False
        logger.info("UpstoxAdapter: Disconnected")

    async def subscribe(self, symbols: list[str]) -> None:
        logger.info(f"UpstoxAdapter: Subscribe stub for {symbols}")

    async def unsubscribe(self, symbols: list[str]) -> None:
        logger.info(f"UpstoxAdapter: Unsubscribe stub for {symbols}")

    async def get_connection_status(self) -> dict:
        return {
            "adapter":             self.name,
            "status":              "CONNECTED" if self.is_connected else "DISCONNECTED",
            "subscribed_symbols":  [],
            "last_tick_at":        None,
        }

    async def get_positions(self) -> list[dict]:
        """Fetch open intraday positions from Upstox to assist with reconciliation."""
        if not self.is_connected:
            return []
            
        token = _fresh_token()
        if not token:
            return []
            
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
        }
        
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                res = await client.get(
                    "https://api.upstox.com/v2/portfolio/short-term-positions",
                    headers=headers
                )
            
            if res.status_code == 200:
                positions = []
                data = res.json().get("data", [])
                for p in data:
                    qty = int(p.get("quantity", p.get("net_quantity", p.get("day_sell_quantity", 0) - p.get("day_buy_quantity", 0)) or 0))
                    # Fallback if net is specifically provided
                    if "quantity" in p:
                        qty = int(p["quantity"])
                    elif "net_quantity" in p:
                        qty = int(p["net_quantity"])
                    
                    if qty != 0:
                        symbol = p.get("trading_symbol", p.get("tradingsymbol", p.get("symbol", "")))
                        positions.append({
                            "symbol": symbol,
                            "quantity": abs(qty),
                            "direction": "LONG" if qty > 0 else "SHORT"
                        })
                return positions
            else:
                logger.error(f"UpstoxAdapter get_positions failed HTTP {res.status_code}: {res.text[:100]}")
        except Exception as e:
            logger.error(f"UpstoxAdapter get_positions error: {e}")
            
        return []

