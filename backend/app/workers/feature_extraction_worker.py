"""
Feature Extraction Worker — Consumes candle data and stores computed features.

Extends WorkerBase to follow the same lifecycle pattern as other workers.
Consumes from market:candles (Redis or Kafka), computes features per symbol,
and stores FeatureSnapshot records in PostgreSQL.

Run:
    python -m app.workers.feature_extraction_worker
"""
from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from datetime import datetime, timezone

import pandas as pd

from app.core.logging import logger
from app.core.streams import STREAM_CANDLES, consume_stream
from app.ml.feature_store.feature_pipeline import FeaturePipeline
from app.workers.base import WorkerBase


class FeatureExtractionWorker(WorkerBase):
    """
    Consumes candle data from Redis/Kafka and stores ML features in PostgreSQL.
    """

    NAME = "feature-extraction-worker"
    CONSUMER_GROUP = "feature_extraction_group"
    CONSUMER_NAME = "feature_extractor_1"
    MAX_BUFFER_SIZE = 200

    def __init__(self):
        super().__init__()
        self._buffers: dict[str, list[dict]] = defaultdict(list)

    async def _handle_candle(self, msg_id: str, data: dict[str, str]):
        """Process a candle and extract features."""
        symbol = data.get("symbol", "")
        if not symbol:
            return

        # Parse candle
        raw_data = data.get("data")
        if raw_data:
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

        self._buffers[symbol].append(candle)
        if len(self._buffers[symbol]) > self.MAX_BUFFER_SIZE:
            self._buffers[symbol] = self._buffers[symbol][-self.MAX_BUFFER_SIZE:]

        # Need minimum candles for feature extraction
        if len(self._buffers[symbol]) < 30:
            return

        # Build DataFrame and extract features
        df = pd.DataFrame(self._buffers[symbol])
        features = FeaturePipeline.extract_features(df, symbol)

        if features:
            await self._store_features(features)

    async def _store_features(self, features: dict):
        """Store extracted features in the database."""
        try:
            from app.core.database import async_session_factory
            from app.ml.feature_store.models import FeatureSnapshot

            async with async_session_factory() as db:
                snapshot = FeatureSnapshot(
                    symbol=features["symbol"],
                    timestamp=features["timestamp"],
                    ema_9=features.get("ema_9"),
                    ema_21=features.get("ema_21"),
                    rsi_14=features.get("rsi_14"),
                    atr_14=features.get("atr_14"),
                    macd_line=features.get("macd_line"),
                    macd_signal=features.get("macd_signal"),
                    vwap=features.get("vwap"),
                    bollinger_upper=features.get("bollinger_upper"),
                    bollinger_lower=features.get("bollinger_lower"),
                    volatility_pct=features.get("volatility_pct"),
                    atr_pct=features.get("atr_pct"),
                    regime=features.get("regime"),
                    trend_strength=features.get("trend_strength"),
                    volume=features.get("volume"),
                    volume_ratio=features.get("volume_ratio"),
                    spread_pct=features.get("spread_pct"),
                )
                db.add(snapshot)
                await db.commit()

        except Exception as e:
            logger.warning(f"[{self.NAME}] Feature store write failed: {e}")

    async def run(self):
        """Main worker loop."""
        await asyncio.sleep(2)

        await consume_stream(
            stream=STREAM_CANDLES,
            group=self.CONSUMER_GROUP,
            consumer=self.CONSUMER_NAME,
            handler=self._handle_candle,
            running=lambda: self.is_running,
        )


# ── Entry Point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    FeatureExtractionWorker().main()
