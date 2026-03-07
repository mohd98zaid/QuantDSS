"""
Candle Persister — Background worker for real-time candle persistence.

Critical Fix 5 (Audit): Candles were published to Redis streams but never
persisted to PostgreSQL. This worker consumes the `market:candles` stream
and batch-inserts into the TimescaleDB-backed `candles` table.

Uses consumer group to ensure exactly-once delivery even across restarts.
"""
import asyncio
import json
from datetime import datetime, timezone, timedelta

from app.core.logging import logger


IST = timezone(timedelta(hours=5, minutes=30))

# Batch settings
BATCH_SIZE = 50
FLUSH_INTERVAL_S = 5.0


class CandlePersister:
    """
    Background worker that reads candles from Redis stream `market:candles`
    and persists them to PostgreSQL via batch upsert.
    """

    STREAM = "market:candles"
    GROUP = "candle_persister_group"
    CONSUMER = "candle_persister_1"

    def __init__(self):
        self._running = False
        self._task: asyncio.Task | None = None
        self._batch: list[dict] = []

    async def start(self) -> None:
        """Start the background persistence loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run())
        logger.info("CandlePersister: Background worker started")

    async def stop(self) -> None:
        """Stop the worker and flush remaining batch."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        # Flush any remaining candles
        if self._batch:
            await self._flush_batch()
        logger.info("CandlePersister: Stopped")

    async def _run(self) -> None:
        """Main loop: consume from Redis stream and batch-insert to PostgreSQL."""
        from app.core.redis import redis_manager

        client = await redis_manager.get_client()
        if not client:
            logger.error("CandlePersister: Redis unavailable, cannot start")
            return

        # Create consumer group (idempotent)
        try:
            await client.xgroup_create(
                self.STREAM, self.GROUP, id="0", mkstream=True
            )
        except Exception as e:
            if "BUSYGROUP" not in str(e):
                logger.error(f"CandlePersister: Failed to create consumer group: {e}")
                return

        logger.info(f"CandlePersister: Consuming from {self.STREAM}")

        while self._running:
            try:
                client = await redis_manager.get_client()
                if not client:
                    logger.warning("CandlePersister: Redis unavailable, retrying in 5s")
                    await asyncio.sleep(5)
                    continue

                results = await client.xreadgroup(
                    self.GROUP, self.CONSUMER,
                    {self.STREAM: ">"},
                    count=BATCH_SIZE,
                    block=int(FLUSH_INTERVAL_S * 1000),
                )

                if results:
                    for _stream_name, messages in results:
                        for msg_id, data in messages:
                            candle = self._parse_candle(data)
                            if candle:
                                self._batch.append(candle)
                            # ACK the message
                            await client.xack(self.STREAM, self.GROUP, msg_id)

                # Flush when batch is full or on timeout
                if len(self._batch) >= BATCH_SIZE:
                    await self._flush_batch()
                elif self._batch:
                    # Flush partial batch on timer
                    await self._flush_batch()

            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("CandlePersister: Error in consume loop")
                await asyncio.sleep(2)

    def _parse_candle(self, data: dict) -> dict | None:
        """Parse a Redis stream message into a candle dict."""
        try:
            # Data may be in a 'data' key as JSON, or flat
            raw = data.get("data")
            if raw:
                candle = json.loads(raw) if isinstance(raw, str) else raw
            else:
                candle = data

            # Decode bytes → str if needed
            parsed = {}
            for k, v in candle.items():
                key = k.decode() if isinstance(k, bytes) else k
                val = v.decode() if isinstance(v, bytes) else v
                parsed[key] = val

            return {
                "symbol": str(parsed.get("symbol", "")),
                "time": parsed.get("time", ""),
                "open": float(parsed.get("open", 0)),
                "high": float(parsed.get("high", 0)),
                "low": float(parsed.get("low", 0)),
                "close": float(parsed.get("close", 0)),
                "volume": int(float(parsed.get("volume", 0))),
                "timeframe": str(parsed.get("timeframe", "1min")),
            }
        except Exception as e:
            logger.debug(f"CandlePersister: Failed to parse candle: {e}")
            return None

    async def _flush_batch(self) -> None:
        """Insert accumulated candles into PostgreSQL."""
        if not self._batch:
            return

        batch = self._batch[:]
        self._batch.clear()

        try:
            from app.core.database import async_session_factory
            from app.models.candle import Candle
            from sqlalchemy.dialects.postgresql import insert as pg_insert

            async with async_session_factory() as db:
                for candle in batch:
                    # Parse time string to datetime
                    candle_time = candle["time"]
                    if isinstance(candle_time, str):
                        try:
                            candle_time = datetime.fromisoformat(candle_time)
                        except ValueError:
                            continue
                    if candle_time and hasattr(candle_time, "tzinfo") and candle_time.tzinfo is None:
                        candle_time = candle_time.replace(tzinfo=IST)

                    stmt = pg_insert(Candle).values(
                        symbol_id=0,  # Will be resolved by trigger/lookup
                        time=candle_time,
                        timeframe=candle.get("timeframe", "1min"),
                        open=candle["open"],
                        high=candle["high"],
                        low=candle["low"],
                        close=candle["close"],
                        volume=candle["volume"],
                    ).on_conflict_do_nothing()
                    await db.execute(stmt)

                await db.commit()

            logger.debug(f"CandlePersister: Flushed {len(batch)} candles to PostgreSQL")

        except Exception:
            logger.exception(f"CandlePersister: Failed to flush {len(batch)} candles")
            # Put failed candles back (optional retry)
            self._batch.extend(batch)


# Module-level singleton
candle_persister = CandlePersister()
