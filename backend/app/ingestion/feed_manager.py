"""
FeedManager — Multi-broker market data feed orchestration.

Manages primary (Upstox) and secondary (AngelOne) feeds with:
  - Feed health monitoring
  - Automatic failover when primary becomes stale
  - Latency tracking per feed source
  - Reconnect coordination

Failover Rules:
  - Primary is Upstox (lowest latency, most reliable)
  - If Upstox stale > 3 seconds, switch to AngelOne
  - If Upstox recovers, switch back automatically
  - Both feeds normalize ticks to the same pipeline format

Usage:
    from app.ingestion.feed_manager import feed_manager
    await feed_manager.start()
"""
from __future__ import annotations

import asyncio
import time
from typing import Callable, Awaitable, Optional

from app.core.config import settings
from app.core.logging import logger


class FeedStats:
    """Track latency and health stats for a single feed."""

    def __init__(self, name: str):
        self.name = name
        self.last_tick_timestamp: float = 0.0  # monotonic
        self.tick_count: int = 0
        self.total_latency_ms: float = 0.0
        self.is_healthy: bool = False

    @property
    def tick_delay_ms(self) -> float:
        """Average tick processing latency in ms."""
        if self.tick_count == 0:
            return 0.0
        return self.total_latency_ms / self.tick_count

    @property
    def age_seconds(self) -> float:
        """Seconds since last tick."""
        if self.last_tick_timestamp == 0:
            return float("inf")
        return time.monotonic() - self.last_tick_timestamp

    def record_tick(self, latency_ms: float = 0.0) -> None:
        self.last_tick_timestamp = time.monotonic()
        self.tick_count += 1
        self.total_latency_ms += latency_ms
        self.is_healthy = True

    def mark_stale(self) -> None:
        self.is_healthy = False


class FeedManager:
    """
    Orchestrates primary and secondary market data feeds.

    The active feed publishes ticks to the candle aggregator pipeline.
    Health monitoring runs every second to detect staleness and trigger failover.
    """

    # Staleness threshold: if no tick for this many seconds, switch feeds
    STALE_THRESHOLD_S = 3.0
    # Health check interval
    MONITOR_INTERVAL_S = 1.0

    def __init__(self):
        self._primary_stats = FeedStats("upstox")
        self._secondary_stats = FeedStats("angelone")
        self._active_feed: str = "upstox"
        self._tick_callback: Optional[Callable[[dict], Awaitable[None]]] = None
        self._monitor_task: Optional[asyncio.Task] = None
        self._upstox_client = None
        self._angelone_client = None

    def set_tick_callback(self, callback: Callable[[dict], Awaitable[None]]) -> None:
        """Set the callback for normalized tick data (e.g., candle_aggregator.process_tick)."""
        self._tick_callback = callback

    async def start(self) -> None:
        """Initialize and start both feeds + health monitor."""
        # Import feeds lazily to avoid circular imports
        from app.ingestion.websocket_manager import ws_manager
        self._upstox_client = ws_manager

        # Only initialize AngelOne if credentials are configured
        if settings.angel_api_key and settings.angel_client_id:
            from app.ingestion.angelone_websocket import AngelOneWebSocketClient
            self._angelone_client = AngelOneWebSocketClient()
            self._angelone_client.set_tick_callback(self._on_secondary_tick)
            await self._angelone_client.connect()
            logger.info("FeedManager: AngelOne secondary feed initialized")
        else:
            logger.info("FeedManager: AngelOne credentials not configured — single-feed mode")

        # Start health monitor
        self._monitor_task = asyncio.create_task(self._health_monitor())
        logger.info(f"FeedManager: Started with active feed = {self._active_feed}")

    async def stop(self) -> None:
        """Stop all feeds and monitoring."""
        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
        if self._angelone_client:
            await self._angelone_client.disconnect()
        logger.info("FeedManager: Stopped")

    async def on_primary_tick(self, tick_data: dict) -> None:
        """
        Called by the Upstox WebSocket client for each normalized tick.

        Records stats and forwards to the pipeline if this is the active feed.
        """
        start = time.monotonic()
        self._primary_stats.record_tick()

        if self._active_feed == "upstox" and self._tick_callback:
            tick_data["feed_source"] = "upstox"
            await self._tick_callback(tick_data)

        latency = (time.monotonic() - start) * 1000
        self._primary_stats.total_latency_ms += latency

    async def _on_secondary_tick(self, tick_data: dict) -> None:
        """
        Called by the AngelOne WebSocket client for each normalized tick.

        Records stats and forwards to the pipeline if this is the active feed.
        """
        self._secondary_stats.record_tick()

        if self._active_feed == "angelone" and self._tick_callback:
            tick_data["feed_source"] = "angelone"
            await self._tick_callback(tick_data)

    async def _health_monitor(self) -> None:
        """Periodic health check and failover logic."""
        while True:
            try:
                await asyncio.sleep(self.MONITOR_INTERVAL_S)

                primary_age = self._primary_stats.age_seconds
                secondary_age = self._secondary_stats.age_seconds

                # Check upstox staleness
                if primary_age > self.STALE_THRESHOLD_S:
                    self._primary_stats.mark_stale()

                    if (
                        self._active_feed == "upstox"
                        and self._angelone_client
                        and secondary_age < self.STALE_THRESHOLD_S
                    ):
                        self._active_feed = "angelone"
                        logger.warning(
                            f"FeedManager: ⚠️ FAILOVER — Upstox stale ({primary_age:.1f}s), "
                            f"switching to AngelOne"
                        )
                elif self._active_feed == "angelone" and primary_age < self.STALE_THRESHOLD_S:
                    # Upstox recovered — switch back
                    self._active_feed = "upstox"
                    logger.info("FeedManager: ✅ Upstox recovered — switching back to primary")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception(f"FeedManager: Health monitor error — {e}")
                await asyncio.sleep(5)

    @property
    def active_feed(self) -> str:
        return self._active_feed

    def get_stats(self) -> dict:
        """Return feed health statistics."""
        return {
            "active_feed": self._active_feed,
            "upstox": {
                "healthy": self._primary_stats.is_healthy,
                "age_seconds": round(self._primary_stats.age_seconds, 1),
                "tick_count": self._primary_stats.tick_count,
                "avg_latency_ms": round(self._primary_stats.tick_delay_ms, 2),
            },
            "angelone": {
                "healthy": self._secondary_stats.is_healthy,
                "age_seconds": round(self._secondary_stats.age_seconds, 1),
                "tick_count": self._secondary_stats.tick_count,
                "avg_latency_ms": round(self._secondary_stats.tick_delay_ms, 2),
            },
        }


# ── Module-level singleton ──────────────────────────────────────────────────
feed_manager = FeedManager()
