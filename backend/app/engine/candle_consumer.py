"""
Redis Stream Consumer for Live Candles — Fix C-05.

Bridges the gap between CandleAggregator (which publishes to Redis Stream
"market:candles") and the Signal Intelligence System.

Reads candles from the stream, maintains a rolling DataFrame buffer per symbol,
evaluates strategies using a SHARED StrategyRunner (loaded once at startup),
and feeds CandidateSignals into the intelligence pipeline via CandidateSignalPool.

Corrective Refactor:
  - StrategyRunner is now a singleton loaded from DB at startup (not per-message).
  - SignalDeduplicator is wired before signals enter the pool.
  - SignalTracer records trace events for debugging.
"""
import asyncio
import json
from collections import defaultdict
from datetime import datetime, timezone, timedelta

import pandas as pd
from sqlalchemy import select

from app.core.logging import logger
from app.core.redis import redis_client
from app.engine.signal_dedup import signal_dedup
from app.engine.signal_trace import SignalTracer


class CandleConsumer:
    """
    Async background service that consumes the 'market:candles' Redis Stream
    and feeds candles into the strategy evaluation pipeline.
    """

    STREAM_KEY = "market:candles"
    GROUP_NAME = "signal_pipeline_group"
    CONSUMER_NAME = "candle_consumer_1"
    MAX_BUFFER_SIZE = 300  # max candles per symbol to retain

    def __init__(self):
        self._task: asyncio.Task | None = None
        self._running = False
        self._buffers: dict[str, list[dict]] = defaultdict(list)
        self._runner = None  # Shared StrategyRunner — loaded once at startup

    # ── Startup ──────────────────────────────────────────────────────────────

    async def load_strategies(self):
        """
        Load all active strategies from the database into a shared StrategyRunner.
        Called ONCE at application startup.
        """
        from app.core.database import async_session_factory
        from app.models.strategy import Strategy
        from app.engine.strategy_runner import StrategyRunner

        runner = StrategyRunner()
        try:
            async with async_session_factory() as db:
                result = await db.execute(
                    select(Strategy).where(Strategy.is_active == True)  # noqa: E712
                )
                strategies = result.scalars().all()

                for strat in strategies:
                    params = strat.parameters or {}
                    runner.load_strategy(strat.id, strat.type, params)

            self._runner = runner
            count = len(runner.loaded_strategies)
            logger.info(
                f"CandleConsumer: loaded {count} strategies from DB: "
                f"{runner.loaded_strategies}"
            )
            if count == 0:
                logger.warning(
                    "CandleConsumer: NO strategies loaded — "
                    "real-time signal generation will be inactive. "
                    "Add strategies via POST /api/v1/strategies."
                )
        except Exception as e:
            logger.exception(f"CandleConsumer: strategy loading failed: {e}")
            self._runner = StrategyRunner()  # Empty runner as fallback

    def start(self):
        """Start the consumer as a background asyncio task."""
        if self._task and not self._task.done():
            logger.warning("CandleConsumer already running")
            return
        self._running = True
        self._task = asyncio.create_task(self._consume_loop())
        logger.info("CandleConsumer started")

    def stop(self):
        """Stop the consumer gracefully."""
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None
        logger.info("CandleConsumer stopped")

    # ── Consumer Group Setup ─────────────────────────────────────────────────

    async def _ensure_group(self):
        """Create the consumer group if it doesn't exist."""
        try:
            r = await redis_client.get_client()
            if r is None:
                return False
            await r.xgroup_create(
                self.STREAM_KEY, self.GROUP_NAME,
                id="0", mkstream=True,
            )
            logger.info(f"CandleConsumer: created consumer group '{self.GROUP_NAME}'")
        except Exception as e:
            if "BUSYGROUP" in str(e):
                pass  # Group already exists — OK
            else:
                logger.warning(f"CandleConsumer: group creation failed: {e}")
                return False
        return True

    # ── Main Loop ────────────────────────────────────────────────────────────

    async def _consume_loop(self):
        """Main consumer loop — reads from Redis Stream and processes candles."""
        await asyncio.sleep(2)  # Wait for startup to settle

        if not await self._ensure_group():
            logger.error("CandleConsumer: cannot start without Redis consumer group")
            return

        r = await redis_client.get_client()
        if r is None:
            logger.error("CandleConsumer: Redis client not available")
            return

        logger.info("CandleConsumer: entering main loop")

        while self._running:
            try:
                # XREADGROUP BLOCK for up to 2 seconds
                results = await r.xreadgroup(
                    self.GROUP_NAME, self.CONSUMER_NAME,
                    {self.STREAM_KEY: ">"},
                    count=50, block=2000,
                )

                if not results:
                    continue

                for stream_name, messages in results:
                    for msg_id, data in messages:
                        try:
                            await self._process_candle_message(data)
                            # Acknowledge the message
                            await r.xack(self.STREAM_KEY, self.GROUP_NAME, msg_id)
                        except Exception as e:
                            logger.exception(f"CandleConsumer: error processing message {msg_id}: {e}")

            except asyncio.CancelledError:
                logger.info("CandleConsumer: cancelled, shutting down loop")
                break
            except Exception as e:
                logger.exception(f"CandleConsumer: loop error: {e}")
                await asyncio.sleep(1)

    # ── Message Processing ───────────────────────────────────────────────────

    async def _process_candle_message(self, data: dict):
        """
        Process a single candle message from Redis Stream.
        Deserialize, buffer, build DataFrame, evaluate strategies,
        run dedup, and feed signals into the intelligence pipeline.
        """
        # Decode bytes to str if needed
        decoded = {}
        for k, v in data.items():
            key = k.decode() if isinstance(k, bytes) else k
            val = v.decode() if isinstance(v, bytes) else v
            decoded[key] = val

        symbol = decoded.get("symbol", "")
        symbol_id = int(decoded.get("symbol_id", 0))
        instrument_key = decoded.get("instrument_key", "")

        if not symbol:
            return

        # Parse candle data
        candle = {
            "time": datetime.fromisoformat(decoded.get("time", "")),
            "open": float(decoded.get("open", 0)),
            "high": float(decoded.get("high", 0)),
            "low": float(decoded.get("low", 0)),
            "close": float(decoded.get("close", 0)),
            "volume": int(float(decoded.get("volume", 0))),
        }

        # Buffer the candle
        self._buffers[symbol].append(candle)
        if len(self._buffers[symbol]) > self.MAX_BUFFER_SIZE:
            self._buffers[symbol] = self._buffers[symbol][-self.MAX_BUFFER_SIZE:]

        # Build DataFrame for strategy evaluation
        if len(self._buffers[symbol]) < 30:
            return  # Not enough candles yet

        df = pd.DataFrame(self._buffers[symbol])
        df = df.set_index("time")
        df.index = pd.to_datetime(df.index, utc=True)

        # ── Evaluate strategies using the SHARED runner ──────────────────
        if self._runner is None:
            logger.warning("CandleConsumer: StrategyRunner not loaded — skipping")
            return

        trace_id = SignalTracer.new_trace_id()
        SignalTracer.trace(trace_id, "CANDLE_CONSUMER", symbol, f"Evaluating {len(self._runner.loaded_strategies)} strategies")

        try:
            signals = self._runner.evaluate(df, symbol_id)

            if not signals:
                return

            SignalTracer.trace(trace_id, "STRATEGY_EVAL", symbol, f"{len(signals)} raw signal(s)")

            # ── Deduplication check ──────────────────────────────────────
            from app.engine.signal_pool import signal_pool

            fed_count = 0
            for sig in signals:
                # Check for duplicates before feeding into the pool (Implicitly records if it wasn't a duplicate)
                if await signal_dedup.is_duplicate(sig.symbol_id, sig.strategy_id, sig.candle_time):
                    SignalTracer.trace_drop(
                        trace_id, "DEDUP_CHECK", symbol,
                        f"Duplicate {sig.strategy_name} suppressed"
                    )
                    continue

                # Attach symbol name for downstream layers
                sig.metadata["symbol_name"] = symbol
                sig.metadata["instrument_key"] = instrument_key
                sig.metadata["source"] = "realtime_stream"
                sig.metadata["trace_id"] = trace_id

                await signal_pool.add_signal(sig)
                fed_count += 1

            if fed_count > 0:
                SignalTracer.trace_pass(
                    trace_id, "SIGNAL_POOL", symbol,
                    f"{fed_count} signal(s) queued"
                )
                logger.info(
                    f"CandleConsumer: {fed_count} signal(s) from {symbol} "
                    f"fed into intelligence pipeline (trace={trace_id})"
                )

        except Exception as e:
            logger.exception(f"CandleConsumer: strategy evaluation failed for {symbol}: {e}")


# Module-level singleton
candle_consumer = CandleConsumer()
