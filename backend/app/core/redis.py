"""
QuantDSS Redis — Client + Pub/Sub Helpers
"""
import redis.asyncio as aioredis

from app.core.config import settings

# Async Redis client
redis_client = aioredis.from_url(
    settings.redis_url,
    decode_responses=True,
    max_connections=20,
)


async def get_redis() -> aioredis.Redis:
    """Dependency that yields a Redis connection."""
    return redis_client


async def publish(channel: str, message: str) -> None:
    """Publish a message to a Redis Pub/Sub channel."""
    await redis_client.publish(channel, message)


async def check_redis_health() -> bool:
    """Check if Redis is reachable."""
    try:
        return await redis_client.ping()
    except Exception:
        return False
