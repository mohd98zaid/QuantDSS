"""
ReplayPublisher — Publishes historical candles into the live pipeline.

Loads candles from TimescaleDB and republishes them to the market:candles
stream (Redis/Kafka) at a controlled speed, allowing the entire downstream
pipeline (signal engine, risk engine, autotrader) to process them as if
they were live.

This enables deterministic replay for debugging, strategy validation,
and incident forensics.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from app.core.logging import logger
from app.core.streams import publish_to_stream, STREAM_CANDLES
from app.research.backtest_engine.data_loader import DataLoader


class ReplayPublisher:
    """Publish historical candles to the live pipeline at controlled speed."""

    async def publish_historical(self, session) -> None:
        """
        Load and publish candles for a replay session.

        Args:
            session: ReplaySession object with date range, symbols, speed
        """
        try:
            symbols = session.symbols
            if not symbols:
                symbols = await DataLoader.get_available_symbols()

            total_candles = 0

            for symbol in symbols:
                if not session.is_running:
                    break

                candles = await DataLoader.load_candles(
                    symbol=symbol,
                    start_date=session.start_date,
                    end_date=session.end_date,
                )

                if candles.empty:
                    continue

                total = len(candles)
                for idx, (_, row) in enumerate(candles.iterrows()):
                    if not session.is_running:
                        break

                    # Handle pause
                    while session.is_paused and session.is_running:
                        await asyncio.sleep(0.1)

                    # Format as stream message
                    candle_data = {
                        "symbol": symbol,
                        "time": str(row["time"]),
                        "open": str(row["open"]),
                        "high": str(row["high"]),
                        "low": str(row["low"]),
                        "close": str(row["close"]),
                        "volume": str(int(row["volume"])),
                        "is_replay": "1",
                        "replay_session_id": session.session_id,
                    }

                    await publish_to_stream(STREAM_CANDLES, candle_data)
                    total_candles += 1
                    session.candles_published = total_candles

                    # Speed control: 60 candles/min = 1 per second at 1x
                    if session.speed > 0:
                        delay = 1.0 / session.speed
                        delay = min(delay, 1.0)  # Cap at 1 second max
                        await asyncio.sleep(delay)

                    # Update progress
                    if total > 0:
                        session.progress_pct = (idx + 1) / total * 100

            logger.info(
                f"ReplayPublisher: Session {session.session_id} completed — "
                f"{total_candles} candles published"
            )

        except asyncio.CancelledError:
            logger.info(f"ReplayPublisher: Session {session.session_id} cancelled")
        except Exception as e:
            logger.exception(f"ReplayPublisher: Error in session {session.session_id}: {e}")
        finally:
            session.is_running = False
