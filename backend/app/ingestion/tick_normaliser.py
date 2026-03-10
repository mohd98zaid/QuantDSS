"""
TickNormaliser — Validates tick data and publishes to Redis.
"""
import json
from datetime import UTC, datetime

from app.core.logging import logger
from app.core.redis import publish
from app.ingestion.broker_adapter import NormalisedTick
from app.ingestion.tick_storage import write_tick
import asyncio


class TickNormaliser:
    """Validates incoming ticks and publishes them to Redis Pub/Sub."""

    MAX_TICK_AGE_SECONDS = 5  # Reject stale ticks older than 5 seconds

    async def process(self, tick: NormalisedTick) -> bool:
        """
        Validate a tick and publish to Redis if valid.

        Returns True if tick was published, False if rejected.
        """
        # Validate required fields
        if tick.ltp <= 0:
            logger.warning(f"Rejected tick for {tick.symbol}: invalid price {tick.ltp}")
            return False

        if tick.volume < 0:
            logger.warning(f"Rejected tick for {tick.symbol}: negative volume {tick.volume}")
            return False

        # Reject stale ticks
        now = datetime.now(UTC)
        tick_age = (now - tick.timestamp).total_seconds()
        if tick_age > self.MAX_TICK_AGE_SECONDS:
            logger.debug(f"Rejected stale tick for {tick.symbol}: {tick_age:.1f}s old")
            return False

        # Publish to Redis
        channel = f"market:tick:{tick.symbol}"
        message = json.dumps({
            "symbol": tick.symbol,
            "ltp": tick.ltp,
            "volume": tick.volume,
            "timestamp": tick.timestamp.isoformat(),
            "exchange": tick.exchange,
        })

        await publish(channel, message)
        
        # Fire and forget storage to Data Lake so we don't block ingestion
        asyncio.create_task(write_tick(tick))
        
        return True
