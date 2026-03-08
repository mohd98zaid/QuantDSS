"""
Kafka Client — Async producer and consumer for high-throughput data streams.

Replaces Redis Streams for market data and signal topics when Kafka is enabled.
Falls back gracefully if Kafka is unavailable.

Topics:
    market.candles        — 1-min OHLCV candles (partitioned by symbol)
    signals.candidate     — raw strategy signals
    signals.approved      — intelligence-pipeline approved signals
    signals.risk_passed   — risk-engine approved signals
    signals.executed      — executed trade confirmations

Usage:
    from app.core.kafka_client import kafka_producer, kafka_consumer

    await kafka_producer.send("market.candles", key="RELIANCE", value={...})
    async for msg in kafka_consumer.consume("market.candles", group="signal_engine"):
        process(msg)
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncGenerator, Callable, Awaitable, Optional

from app.core.config import settings
from app.core.logging import logger

# ── Topic Constants ──────────────────────────────────────────────────────────
TOPIC_CANDLES = "market.candles"
TOPIC_SIGNALS_CANDIDATE = "signals.candidate"
TOPIC_SIGNALS_APPROVED = "signals.approved"
TOPIC_SIGNALS_RISK_PASSED = "signals.risk_passed"
TOPIC_SIGNALS_EXECUTED = "signals.executed"

ALL_TOPICS = [
    TOPIC_CANDLES,
    TOPIC_SIGNALS_CANDIDATE,
    TOPIC_SIGNALS_APPROVED,
    TOPIC_SIGNALS_RISK_PASSED,
    TOPIC_SIGNALS_EXECUTED,
]


class KafkaProducerClient:
    """Async Kafka producer with retry logic and JSON serialization."""

    def __init__(self, bootstrap_servers: str = "kafka:9092"):
        self._bootstrap = bootstrap_servers
        self._producer = None
        self._started = False

    async def start(self) -> None:
        """Start the Kafka producer."""
        if self._started:
            return
        try:
            from aiokafka import AIOKafkaProducer
            self._producer = AIOKafkaProducer(
                bootstrap_servers=self._bootstrap,
                value_serializer=lambda v: json.dumps(v).encode("utf-8"),
                key_serializer=lambda k: k.encode("utf-8") if k else None,
                acks="all",
                retries=3,
                retry_backoff_ms=200,
                linger_ms=10,  # batch for 10ms for throughput
                max_batch_size=65536,
                compression_type="lz4",
            )
            await self._producer.start()
            self._started = True
            logger.info(f"KafkaProducer started: {self._bootstrap}")
        except Exception as e:
            logger.warning(f"KafkaProducer start failed (Kafka may be unavailable): {e}")
            self._producer = None

    async def stop(self) -> None:
        """Gracefully stop the producer."""
        if self._producer and self._started:
            try:
                await self._producer.stop()
            except Exception:
                pass
            self._started = False
            logger.info("KafkaProducer stopped")

    async def send(
        self,
        topic: str,
        value: dict[str, Any],
        key: str | None = None,
        retries: int = 3,
    ) -> bool:
        """
        Send a message to a Kafka topic.

        Args:
            topic: Kafka topic name
            value: Message payload (will be JSON-serialized)
            key: Partition key (e.g., symbol name for locality)
            retries: Max retry attempts

        Returns:
            True if sent successfully, False otherwise
        """
        if not self._started or not self._producer:
            return False

        for attempt in range(1, retries + 1):
            try:
                await self._producer.send_and_wait(topic, value=value, key=key)
                return True
            except Exception as e:
                if attempt == retries:
                    logger.error(f"KafkaProducer: Failed to send to {topic} after {retries} attempts: {e}")
                    return False
                await asyncio.sleep(0.1 * attempt)
        return False


class KafkaConsumerClient:
    """Async Kafka consumer with configurable consumer groups."""

    def __init__(self, bootstrap_servers: str = "kafka:9092"):
        self._bootstrap = bootstrap_servers

    async def consume(
        self,
        topic: str,
        group_id: str,
        handler: Callable[[str, dict[str, str]], Awaitable[None]],
        running: Callable[[], bool] | None = None,
    ) -> None:
        """
        Consume messages from a Kafka topic and call handler for each.

        Args:
            topic: Kafka topic to consume
            group_id: Consumer group ID
            handler: Async callback — receives (key, data_dict)
            running: Optional callable returning False to stop the loop
        """
        _should_run = running or (lambda: True)

        try:
            from aiokafka import AIOKafkaConsumer
            consumer = AIOKafkaConsumer(
                topic,
                bootstrap_servers=self._bootstrap,
                group_id=group_id,
                value_deserializer=lambda v: json.loads(v.decode("utf-8")),
                key_deserializer=lambda k: k.decode("utf-8") if k else "",
                auto_offset_reset="latest",
                enable_auto_commit=True,
                auto_commit_interval_ms=1000,
                max_poll_interval_ms=300000,
            )
        except ImportError:
            logger.error("aiokafka not installed — cannot consume from Kafka")
            return
        except Exception as e:
            logger.error(f"KafkaConsumer creation failed: {e}")
            return

        try:
            await consumer.start()
            logger.info(f"KafkaConsumer started: topic={topic}, group={group_id}")

            while _should_run():
                try:
                    result = await asyncio.wait_for(
                        consumer.getmany(timeout_ms=2000, max_records=50),
                        timeout=5.0,
                    )
                    for tp, messages in result.items():
                        for msg in messages:
                            try:
                                key = msg.key or ""
                                data = msg.value if isinstance(msg.value, dict) else {}
                                # Ensure all values are strings for compatibility
                                str_data = {
                                    str(k): str(v) for k, v in data.items()
                                }
                                await handler(key, str_data)
                            except Exception as e:
                                logger.exception(
                                    f"KafkaConsumer handler error on {topic}: {e}"
                                )
                except asyncio.TimeoutError:
                    continue
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.exception(f"KafkaConsumer loop error on {topic}: {e}")
                    await asyncio.sleep(1)

        except asyncio.CancelledError:
            logger.info(f"KafkaConsumer on {topic} cancelled")
        except Exception as e:
            logger.exception(f"KafkaConsumer fatal error on {topic}: {e}")
        finally:
            try:
                await consumer.stop()
            except Exception:
                pass
            logger.info(f"KafkaConsumer on {topic} stopped")


# ── Module-level singletons (lazy-initialized) ──────────────────────────────
kafka_producer: KafkaProducerClient = KafkaProducerClient(
    bootstrap_servers=settings.kafka_bootstrap_servers,
)
kafka_consumer: KafkaConsumerClient = KafkaConsumerClient(
    bootstrap_servers=settings.kafka_bootstrap_servers,
)
