"""
QuantDSS Redis Streams — Centralized stream constants and helpers.

All inter-service communication uses Redis Streams via XADD / XREADGROUP.
This module provides:
  - Stream name constants
  - publish_to_stream(): XADD wrapper with maxlen cap
  - create_consumer_group(): safe XGROUP CREATE (idempotent)
  - consume_stream(): generic async consumer loop

All workers import from here to avoid hard-coded stream names.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncGenerator, Callable, Awaitable

from app.core.logging import logger
from app.core.redis import redis_client


# ── Stream Name Constants ────────────────────────────────────────────────────
STREAM_CANDLES = "market:candles"
STREAM_SIGNALS_CANDIDATE = "signals:candidate"
STREAM_SIGNALS_APPROVED = "signals:approved"
STREAM_SIGNALS_RISK_PASSED = "signals:risk_passed"
STREAM_SIGNALS_EXECUTED = "signals:executed"

# ── Redis Stream → Kafka Topic Mapping ───────────────────────────────────────
_STREAM_TO_KAFKA_TOPIC: dict[str, str] = {
    STREAM_CANDLES: "market.candles",
    STREAM_SIGNALS_CANDIDATE: "signals.candidate",
    STREAM_SIGNALS_APPROVED: "signals.approved",
    STREAM_SIGNALS_RISK_PASSED: "signals.risk_passed",
    STREAM_SIGNALS_EXECUTED: "signals.executed",
}

# Default maxlen for all streams (approximate trimming)
DEFAULT_MAXLEN = 10_000


# ── Publish ──────────────────────────────────────────────────────────────────

async def publish_to_stream(
    stream: str,
    data: dict[str, Any],
    maxlen: int = DEFAULT_MAXLEN,
) -> str | None:
    """
    Publish a message to a Redis Stream via XADD.

    Args:
        stream: Stream name (use constants above).
        data: Dict of string key-value pairs to publish.
        maxlen: Approximate max stream length (auto-trimmed).

    Returns:
        The message ID assigned by Redis, or None on error.
    """
    try:
        # Ensure all values are strings (Redis streams require str/bytes)
        flat: dict[str, str] = {}
        for k, v in data.items():
            if isinstance(v, (dict, list)):
                flat[k] = json.dumps(v)
            elif v is None:
                flat[k] = ""
            else:
                flat[k] = str(v)

        msg_id = await redis_client.xadd(
            stream, flat, maxlen=maxlen, approximate=True,
        )

        # Dual-write to Kafka when enabled
        try:
            from app.core.config import settings
            if settings.kafka_enabled:
                from app.core.kafka_client import kafka_producer
                kafka_topic = _STREAM_TO_KAFKA_TOPIC.get(stream)
                if kafka_topic:
                    symbol_key = flat.get("symbol", flat.get("symbol_name", ""))
                    await kafka_producer.send(
                        kafka_topic, value=flat, key=symbol_key or None,
                    )
        except Exception as kafka_err:
            logger.debug(f"Kafka dual-write to {stream} failed (non-critical): {kafka_err}")

        return msg_id
    except Exception as e:
        logger.exception(f"publish_to_stream({stream}) failed: {e}")
        return None


# ── Consumer Group Management ────────────────────────────────────────────────

async def create_consumer_group(
    stream: str,
    group: str,
    start_id: str = "0",
) -> bool:
    """
    Create a consumer group for a stream. Safe to call repeatedly —
    silently ignores BUSYGROUP errors (group already exists).

    Args:
        stream: Redis stream name.
        group: Consumer group name.
        start_id: Starting message ID ("0" = read all history, "$" = new only).

    Returns:
        True if group was created or already existed.
    """
    try:
        await redis_client.xgroup_create(
            stream, group, id=start_id, mkstream=True,
        )
        logger.info(f"Created consumer group '{group}' on stream '{stream}'")
        return True
    except Exception as e:
        if "BUSYGROUP" in str(e):
            return True  # Already exists — OK
        logger.exception(f"Failed to create consumer group '{group}' on '{stream}': {e}")
        return False


# ── Generic Consumer Loop ────────────────────────────────────────────────────

async def consume_stream(
    stream: str,
    group: str,
    consumer: str,
    handler: Callable[[str, dict[str, str]], Awaitable[None]],
    batch_size: int = 50,
    block_ms: int = 2000,
    running: Callable[[], bool] | None = None,
) -> None:
    """
    Generic async consumer loop for a Redis Stream.

    Reads messages via XREADGROUP and calls `handler(msg_id, data)` for each.
    Automatically acknowledges messages after successful handling.

    Args:
        stream: Redis stream name.
        group: Consumer group name.
        consumer: Consumer name within the group.
        handler: Async callback — receives (message_id, data_dict).
        batch_size: Max messages per XREADGROUP call.
        block_ms: Milliseconds to block waiting for new messages.
        running: Optional callable returning False to stop the loop.
    """
    if not await create_consumer_group(stream, group):
        logger.error(f"Cannot start consumer — group creation failed for {stream}/{group}")
        return

    _should_run = running or (lambda: True)

    logger.info(f"Consumer '{consumer}' entering loop on '{stream}' (group={group})")

    while _should_run():
        try:
            results = await redis_client.xreadgroup(
                group, consumer,
                {stream: ">"},
                count=batch_size,
                block=block_ms,
            )

            if not results:
                continue

            for _stream_name, messages in results:
                for msg_id, data in messages:
                    # Decode bytes → str if needed
                    decoded: dict[str, str] = {}
                    for k, v in data.items():
                        key = k.decode() if isinstance(k, bytes) else k
                        val = v.decode() if isinstance(v, bytes) else v
                        decoded[key] = val

                    try:
                        await handler(msg_id, decoded)
                        # ACK on success
                        await redis_client.xack(stream, group, msg_id)
                    except Exception as e:
                        logger.exception(
                            f"Consumer '{consumer}' error processing {msg_id} on {stream}: {e}"
                        )

        except asyncio.CancelledError:
            logger.info(f"Consumer '{consumer}' on '{stream}' cancelled — exiting")
            break
        except Exception as e:
            logger.exception(f"Consumer '{consumer}' loop error on '{stream}': {e}")
            await asyncio.sleep(1)

    logger.info(f"Consumer '{consumer}' on '{stream}' stopped")


# ── Helpers ──────────────────────────────────────────────────────────────────

def decode_stream_message(data: dict) -> dict[str, str]:
    """Decode a raw Redis stream message (bytes → str)."""
    return {
        (k.decode() if isinstance(k, bytes) else k): (
            v.decode() if isinstance(v, bytes) else v
        )
        for k, v in data.items()
    }


# ── Kafka Consumer Helper ────────────────────────────────────────────────────

async def consume_from_kafka(
    stream: str,
    group: str,
    handler: Callable[[str, dict[str, str]], Awaitable[None]],
    running: Callable[[], bool] | None = None,
) -> None:
    """
    Consume messages from a Kafka topic instead of Redis Stream.

    Uses the same handler signature as consume_stream() for drop-in compatibility.
    The `stream` parameter is automatically mapped to its Kafka topic equivalent.
    """
    from app.core.kafka_client import kafka_consumer

    kafka_topic = _STREAM_TO_KAFKA_TOPIC.get(stream, stream.replace(":", "."))

    await kafka_consumer.consume(
        topic=kafka_topic,
        group_id=group,
        handler=handler,
        running=running,
    )
