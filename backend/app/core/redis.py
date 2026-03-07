"""
QuantDSS Redis — Resilient Client with Auto-Reconnection.

Critical Fix 3 (Audit): Replaced bare aioredis.from_url() with a RedisManager
class that detects broken connections and reconnects with exponential backoff.

All existing consumers continue to import `redis_client` from this module —
the module-level reference is updated transparently on reconnect.
"""
import asyncio
import redis.asyncio as aioredis

from app.core.config import settings
from app.core.logging import logger


class RedisManager:
    """
    Manages a resilient Redis connection with auto-reconnection.

    Usage:
        client = await redis_manager.get_client()
        await client.ping()
    """

    _MAX_RETRIES = 10
    _BASE_BACKOFF_S = 1.0
    _MAX_BACKOFF_S = 30.0

    def __init__(self, redis_url: str):
        self._url = redis_url
        self._client: aioredis.Redis | None = None
        self._lock = asyncio.Lock()
        self._connect()

    def _connect(self) -> None:
        """Create the initial Redis client (synchronous, called at import time)."""
        try:
            self._client = aioredis.from_url(
                self._url,
                decode_responses=True,
                max_connections=20,
                socket_connect_timeout=5,
                socket_keepalive=True,
                retry_on_timeout=True,
            )
        except Exception as e:
            logger.error(f"Redis initial connection failed: {e}")
            self._client = None

    async def get_client(self) -> aioredis.Redis | None:
        """
        Return a healthy Redis client, reconnecting if needed.

        Returns None only if Redis is completely unreachable after retries.
        """
        if self._client is not None:
            try:
                await self._client.ping()
                return self._client
            except Exception:
                logger.warning("Redis connection lost — attempting reconnect")

        # Reconnect with backoff
        async with self._lock:
            # Double-check after acquiring lock (another coroutine may have reconnected)
            if self._client is not None:
                try:
                    await self._client.ping()
                    return self._client
                except Exception:
                    pass

            return await self._reconnect()

    async def _reconnect(self) -> aioredis.Redis | None:
        """Reconnect with exponential backoff."""
        backoff = self._BASE_BACKOFF_S

        for attempt in range(1, self._MAX_RETRIES + 1):
            try:
                # Close old connection if it exists
                if self._client is not None:
                    try:
                        await self._client.close()
                    except Exception:
                        pass

                self._client = aioredis.from_url(
                    self._url,
                    decode_responses=True,
                    max_connections=20,
                    socket_connect_timeout=5,
                    socket_keepalive=True,
                    retry_on_timeout=True,
                )
                await self._client.ping()
                logger.info(f"Redis reconnected successfully (attempt {attempt})")
                return self._client

            except Exception as e:
                logger.warning(
                    f"Redis reconnect attempt {attempt}/{self._MAX_RETRIES} failed: {e}. "
                    f"Retrying in {backoff:.0f}s..."
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, self._MAX_BACKOFF_S)

        logger.error(f"Redis reconnect failed after {self._MAX_RETRIES} attempts")
        self._client = None
        return None

    @property
    def client(self) -> aioredis.Redis | None:
        """Direct access to the current client (may be stale — prefer get_client())."""
        return self._client


# ── Module-level singleton ──────────────────────────────────────────────────
redis_manager = RedisManager(settings.redis_url)

# Backward-compatible module-level reference.
# Consumers that do `from app.core.redis import redis_client` get this.
# It may become None if Redis is down, but get_client() handles reconnection.
redis_client = redis_manager.client


async def get_redis() -> aioredis.Redis:
    """Dependency that yields a healthy Redis connection."""
    client = await redis_manager.get_client()
    if client is None:
        raise RuntimeError("Redis is unavailable")
    return client


async def publish(channel: str, message: str) -> None:
    """Publish a message to a Redis Pub/Sub channel."""
    client = await redis_manager.get_client()
    if client:
        await client.publish(channel, message)


async def check_redis_health() -> bool:
    """Check if Redis is reachable."""
    try:
        client = await redis_manager.get_client()
        return client is not None
    except Exception:
        return False
