"""
Signal Engine Worker — Standalone service (Phase 4).

Consumes candles from the `market:candles` Redis stream, evaluates strategies,
runs the full signal intelligence pipeline (dedup → pool → consolidation →
confirmation → quality → ML → NLP → time filter), and publishes approved
signals to the `signals:approved` stream.

Run:
    python -m app.workers.signal_engine_worker
"""
from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, timezone, timedelta

import pandas as pd

from app.core.logging import logger
from app.core.streams import (
    STREAM_CANDLES,
    STREAM_SIGNALS_CANDIDATE,
    STREAM_SIGNALS_APPROVED,
    consume_stream,
    publish_to_stream,
)
from app.engine.base_strategy import CandidateSignal
from app.engine.consolidation_layer import ConsolidatedSignal
from app.engine.sharding import ShardManager
from app.workers.base import WorkerBase


class SignalEngineWorker(WorkerBase):
    """
    Consumes candle data from Redis, evaluates trading strategies,
    and pushes approved signals through the intelligence pipeline.
    """

    NAME = "signal-engine-worker"
    CONSUMER_GROUP = "signal_engine_group"
    MAX_BUFFER_SIZE = 300

    def __init__(self):
        super().__init__()
        self._buffers: dict[str, list[dict]] = defaultdict(list)
        self._strategy_runner = None
        # Shard-aware consumer naming
        from app.core.config import settings
        self._shard = ShardManager(
            worker_id=settings.signal_worker_id,
            total_workers=settings.signal_worker_total,
        )
        self.CONSUMER_NAME = f"signal_engine_{settings.signal_worker_id}"

    # ── Startup ──────────────────────────────────────────────────────────────

    async def _init_strategies(self):
        """Load strategies from DB and initialize the StrategyRunner."""
        from app.engine.strategy_runner import StrategyRunner

        self._strategy_runner = StrategyRunner()
        logger.info(f"[{self.NAME}] StrategyRunner loaded with strategies")

    async def _init_intelligence_pipeline(self):
        """
        Wire the signal intelligence pipeline layers.
        
        Pipeline order:
          CandidateSignalPool → ConsolidationLayer → ConfirmationLayer →
          QualityScoreLayer → MLFilter → NLPFilter → TimeFilter →
          _on_signal_approved (publishes to Redis)
        """
        from app.engine.signal_pool import signal_pool
        from app.engine.consolidation_layer import consolidation_layer
        from app.engine.meta_strategy_engine import meta_strategy_engine
        from app.engine.confirmation_layer import confirmation_layer
        from app.engine.quality_score_layer import quality_score_layer
        from app.engine.ml_filter_layer import ml_filter_layer
        from app.engine.nlp_filter_layer import nlp_filter_layer
        from app.engine.time_filter_layer import time_filter_layer

        # Wire callbacks: each layer calls the next
        signal_pool.set_callback(consolidation_layer.process_signal_group)
        consolidation_layer.set_callback(meta_strategy_engine.filter_signal)
        meta_strategy_engine.set_callback(confirmation_layer.verify_confirmation)
        confirmation_layer.set_callback(quality_score_layer.score_signal)
        quality_score_layer.set_callback(ml_filter_layer.evaluate)
        ml_filter_layer.set_callback(nlp_filter_layer.evaluate)
        nlp_filter_layer.set_callback(time_filter_layer.evaluate)

        # Terminal action: publish to Redis stream instead of FinalAlertGenerator
        time_filter_layer.set_callback(self._on_signal_approved)

        signal_pool.start()
        self._signal_pool = signal_pool

        logger.info(f"[{self.NAME}] Intelligence pipeline wired and started")

    # ── Terminal action: publish approved signal to Redis ─────────────────────

    async def _on_signal_approved(self, signal: ConsolidatedSignal):
        """
        Called when a signal passes the entire intelligence pipeline.
        Publishes to signals:approved and persists to DB.
        """
        sym_name = getattr(signal, "symbol_name", "UNKNOWN")
        strat_name = getattr(signal, "primary_strategy", "UNKNOWN")
        
        # Structlog context injection
        import structlog
        structlog.contextvars.bind_contextvars(
            symbol=sym_name,
            strategy=strat_name,
            signal_id=str(getattr(signal, "signal_id", ""))
        )

        # Build message for Redis stream
        contributing = list(signal.contributing_signals.keys())
        # Use the best entry/SL/target from contributing signals
        primary = list(signal.contributing_signals.values())[0]
        
        if not sym_name or sym_name == "UNKNOWN":
            sym_name = getattr(primary, "metadata", {}).get("symbol_name", str(sym_name))
            if not sym_name or sym_name == "UNKNOWN":
                sym_name = getattr(primary, "symbol_name", "UNKNOWN")

        message = {
            "symbol_id": str(signal.symbol_id),
            "symbol_name": sym_name,
            "signal_type": signal.signal_type,
            "entry_price": str(primary.entry_price),
            "stop_loss": str(primary.stop_loss),
            "target_price": str(primary.target_price),
            "atr_value": str(primary.atr_value),
            "candle_time": primary.candle_time.isoformat() if primary.candle_time else "",
            "contributing_strategies": json.dumps(contributing),
            "quality_score": str(getattr(signal, "quality_score", 0)),
            "ml_probability": str(getattr(signal, "ml_probability", "")),
            "nlp_sentiment": str(getattr(signal, "nlp_sentiment", "")),
            "total_weight": str(getattr(signal, "total_weight", 0)),
            # Carry replay context through the pipeline
            "is_replay": str(getattr(signal, "is_replay", False)),
            "replay_session_id": str(getattr(signal, "replay_session_id", "")),
            # Carry manual override context
            "is_scanner": str(getattr(primary, "metadata", {}).get("source") == "scanner"),
        }

        # ── Global Kill Switch Check (Soft Block) ──
        from app.core.redis import redis_client
        from app.system.trading_state import is_trading_enabled
        
        trading_enabled = await is_trading_enabled(redis_client)
        if not trading_enabled:
            # Mark signal as blocked, but still save it
            message["risk_status"] = "blocked_kill_switch"
            status_for_db = "BLOCKED_KILL_SWITCH"
            local_logger.warning(
                f"[{self.NAME}] Signal soft-blocked by global kill switch: "
                f"{sym_name} {signal.signal_type}"
            )
        else:
            message["risk_status"] = "APPROVED"
            status_for_db = "APPROVED"

        msg_id = await publish_to_stream(STREAM_SIGNALS_APPROVED, message)
        
        local_logger = logger.bind(symbol=sym_name, signal_id=msg_id)

        if msg_id:
            local_logger.info(
                f"[{self.NAME}] ✅ Published approved signal: {sym_name} "
                f"{signal.signal_type} → {STREAM_SIGNALS_APPROVED}"
            )

        # Also persist to DB for UI visibility
        try:
            from app.engine.final_alert_generator import _persist_signal_to_db
            await _persist_signal_to_db(signal, status=status_for_db)
        except Exception as e:
            logger.warning(f"[{self.NAME}] DB persist failed (non-critical): {e}")

        # Also publish SSE event for real-time UI
        try:
            from app.alerts.sse_manager import SSEManager
            await SSEManager.publish_signal_event({
                "signal_type": signal.signal_type,
                "symbol": sym_name,
                "entry_price": float(primary.entry_price),
                "stop_loss": float(primary.stop_loss),
                "target_price": float(primary.target_price),
                "quality_score": getattr(signal, "quality_score", None),
                "risk_status": status_for_db,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
        except Exception:
            pass

    # ── Candle Message Handler ───────────────────────────────────────────────

    async def _handle_candle(self, msg_id: str, data: dict[str, str]):
        """Process a single candle message from the Redis stream."""
        symbol = data.get("symbol", "")
        # Bind context to local logger
        local_logger = logger.bind(symbol=symbol, msg_id=msg_id)
        
        symbol_id = int(data.get("symbol_id", "0"))
        instrument_key = data.get("instrument_key", "")
        is_replay = data.get("is_replay", "0") == "1"
        replay_session_id = data.get("replay_session_id", "")

        if not symbol:
            return

        # Shard check: skip symbols not owned by this worker
        if not self._shard.owns(symbol):
            return

        # Parse candle — handle both raw field format and nested JSON 'data' field
        raw_data = data.get("data")
        if raw_data:
            import json
            candle_dict = json.loads(raw_data)
        else:
            candle_dict = data

        candle = {
            "time": datetime.fromisoformat(candle_dict.get("time", data.get("time", ""))),
            "open": float(candle_dict.get("open", data.get("open", "0"))),
            "high": float(candle_dict.get("high", data.get("high", "0"))),
            "low": float(candle_dict.get("low", data.get("low", "0"))),
            "close": float(candle_dict.get("close", data.get("close", "0"))),
            "volume": int(float(candle_dict.get("volume", data.get("volume", "0")))),
        }

        # Buffer the candle
        self._buffers[symbol].append(candle)
        if len(self._buffers[symbol]) > self.MAX_BUFFER_SIZE:
            self._buffers[symbol] = self._buffers[symbol][-self.MAX_BUFFER_SIZE:]

        # Need minimum candles for strategy evaluation (Fix Group 6)
        if len(self._buffers[symbol]) < 120:
            return

        # Build DataFrame
        df = pd.DataFrame(self._buffers[symbol])
        df = df.set_index("time")
        df.index = pd.to_datetime(df.index, utc=True)

        # Evaluate strategies
        try:
            signals = await self._strategy_runner.evaluate(df, symbol_id)

            if signals:
                for sig in signals:
                    sig.symbol_name = symbol
                    sig.metadata = getattr(sig, "metadata", {})
                    if hasattr(sig, "metadata"):
                        sig.metadata["instrument_key"] = instrument_key
                        sig.metadata["source"] = "replay_stream" if is_replay else "realtime_stream"
                    # Carry replay context through to the signal object
                    sig.is_replay = is_replay
                    sig.replay_session_id = replay_session_id

                    from app.engine.signal_dedup import signal_dedup
                    if await signal_dedup.is_duplicate(
                        sig.symbol_id,
                        sig.strategy_id,
                        sig.candle_time
                    ):
                        logger.debug(f"[{self.NAME}] Duplicate signal skipped: {sig.symbol_name} (strat: {sig.strategy_id})")
                        continue

                    await self._signal_pool.add_signal(sig)

                logger.info(
                    f"[{self.NAME}] {len(signals)} signal(s) from {symbol} "
                    f"fed into intelligence pipeline"
                )
        except Exception as e:
            logger.exception(f"[{self.NAME}] Strategy evaluation failed for {symbol}: {e}")

    # ── Candidate Signal Message Handler (Scanner signals) ───────────────────

    async def _handle_candidate_signal(self, msg_id: str, data: dict[str, str]):
        """Process a scanner candidate signal from the Redis stream."""
        try:
            symbol = data.get("symbol_name", "")
            if not symbol or not self._shard.owns(symbol):
                return
            
            payload = data.get("payload")
            if not payload:
                return
                
            from app.engine.signal_pool import _deserialize_signal
            sig = _deserialize_signal(payload)
            
            # Structlog context injection
            import structlog
            structlog.contextvars.bind_contextvars(
                symbol=symbol,
                strategy=sig.strategy_name,
                signal_id=msg_id
            )
                
            from app.engine.signal_pool import _deserialize_signal
            sig = _deserialize_signal(payload)
            await self._signal_pool.add_signal(sig)
            
            logger.info(f"[{self.NAME}] Ingested {sig.strategy_name} scanner signal for {symbol} into pipeline")
        except Exception as e:
            logger.exception(f"[{self.NAME}] Failed to ingest candidate signal: {e}")

    # ── Main Loop ────────────────────────────────────────────────────────────

    async def run(self):
        """Main worker loop — consume candles and process signals."""
        await self._init_strategies()
        await self._init_intelligence_pipeline()

        # Wait briefly for infrastructure to settle
        await asyncio.sleep(2)

        # Run two consumer loops concurrently: one for candles, one for candidate signals
        await asyncio.gather(
            consume_stream(
                stream=STREAM_CANDLES,
                group=self.CONSUMER_GROUP,
                consumer=self.CONSUMER_NAME,
                handler=self._handle_candle,
                running=lambda: self.is_running,
            ),
            consume_stream(
                stream=STREAM_SIGNALS_CANDIDATE,
                group=self.CONSUMER_GROUP + "_candidates",
                consumer=self.CONSUMER_NAME,
                handler=self._handle_candidate_signal,
                running=lambda: self.is_running,
            )
        )

    async def teardown(self):
        """Cleanup on shutdown."""
        try:
            self._signal_pool.stop()
        except Exception:
            pass


# ── Entry Point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    SignalEngineWorker().main()
