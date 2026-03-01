"""
SSEManager — Server-Sent Events for real-time dashboard push.
Signal events are published to Redis and consumed by SSE endpoints.
"""
import asyncio
import json
from collections.abc import AsyncGenerator
from datetime import UTC, datetime

from app.core.logging import logger
from app.core.redis import redis_client


class SSEManager:
    """
    Manages Server-Sent Events for real-time dashboard updates.
    Uses Redis Pub/Sub as the event bus.
    """

    CHANNEL = "sse:signals"

    @classmethod
    async def publish_signal_event(cls, event_data: dict) -> None:
        """Publish a signal event to the SSE channel."""
        message = json.dumps({
            "type": "signal",
            "timestamp": datetime.now(UTC).isoformat(),
            **event_data,
        })
        await redis_client.publish(cls.CHANNEL, message)
        logger.debug(f"SSE published: {event_data.get('signal_type', 'unknown')}")

    @classmethod
    async def publish_risk_event(cls, event_data: dict) -> None:
        """Publish a risk state change event."""
        message = json.dumps({
            "type": "risk_update",
            "timestamp": datetime.now(UTC).isoformat(),
            **event_data,
        })
        await redis_client.publish(cls.CHANNEL, message)

    @classmethod
    async def publish_halt_event(cls, reason: str) -> None:
        """Publish a trading halt event."""
        message = json.dumps({
            "type": "halt",
            "timestamp": datetime.now(UTC).isoformat(),
            "reason": reason,
        })
        await redis_client.publish(cls.CHANNEL, message)

    @classmethod
    async def event_stream(cls) -> AsyncGenerator[str, None]:
        """
        Async generator that yields SSE-formatted events.
        Subscribes to Redis Pub/Sub and yields events as they arrive.
        """
        pubsub = redis_client.pubsub()
        await pubsub.subscribe(cls.CHANNEL)

        try:
            # Send initial connection confirmation
            yield f"event: connected\ndata: {json.dumps({'status': 'connected'})}\n\n"

            while True:
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True,
                    timeout=1.0,
                )

                if message and message["type"] == "message":
                    data = message["data"]
                    if isinstance(data, bytes):
                        data = data.decode("utf-8")
                    yield f"event: signal\ndata: {data}\n\n"

                # Heartbeat every 30 seconds to keep connection alive
                yield f"event: heartbeat\ndata: {json.dumps({'ts': datetime.now(UTC).isoformat()})}\n\n"
                await asyncio.sleep(1)

        except asyncio.CancelledError:
            pass
        finally:
            await pubsub.unsubscribe(cls.CHANNEL)
            await pubsub.close()
