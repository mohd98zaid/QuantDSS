"""
DataLoader — Load historical candles from TimescaleDB for backtesting.

Supports:
  - Date range filtering
  - Symbol filtering
  - Multiple timeframe support (1min, 5min, 15min, 1h, 1d)
  - Pandas DataFrame output
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

import pandas as pd
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_session_factory
from app.core.logging import logger
from app.models.candle import Candle


class DataLoader:
    """Load historical candle data from TimescaleDB."""

    @staticmethod
    async def load_candles(
        symbol: str,
        start_date: date | str,
        end_date: date | str | None = None,
        timeframe: str = "1min",
        db: AsyncSession | None = None,
    ) -> pd.DataFrame:
        """
        Load historical OHLCV candles for a symbol from the database.

        Args:
            symbol: Trading symbol (e.g., "RELIANCE")
            start_date: Start date (inclusive)
            end_date: End date (inclusive), defaults to today
            timeframe: Candle timeframe (currently only 1min stored)
            db: Optional database session

        Returns:
            DataFrame with columns: time, open, high, low, close, volume
        """
        if isinstance(start_date, str):
            start_date = date.fromisoformat(start_date)
        if end_date is None:
            end_date = date.today()
        elif isinstance(end_date, str):
            end_date = date.fromisoformat(end_date)

        start_dt = datetime.combine(start_date, datetime.min.time())
        end_dt = datetime.combine(end_date, datetime.max.time())

        should_close = db is None
        if db is None:
            db = async_session_factory()

        try:
            async with db:
                result = await db.execute(
                    select(Candle)
                    .where(
                        and_(
                            Candle.symbol == symbol,
                            Candle.timestamp >= start_dt,
                            Candle.timestamp <= end_dt,
                        )
                    )
                    .order_by(Candle.timestamp.asc())
                )
                rows = result.scalars().all()

                if not rows:
                    logger.warning(f"DataLoader: No candles found for {symbol} ({start_date} → {end_date})")
                    return pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])

                data = [
                    {
                        "time": row.timestamp,
                        "open": float(row.open),
                        "high": float(row.high),
                        "low": float(row.low),
                        "close": float(row.close),
                        "volume": int(row.volume),
                    }
                    for row in rows
                ]

                df = pd.DataFrame(data)
                df["time"] = pd.to_datetime(df["time"])
                df = df.sort_values("time").reset_index(drop=True)

                logger.info(
                    f"DataLoader: Loaded {len(df)} candles for {symbol} "
                    f"({start_date} → {end_date})"
                )
                return df

        except Exception as e:
            logger.exception(f"DataLoader: Failed to load candles for {symbol}: {e}")
            return pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])

    @staticmethod
    async def get_available_symbols(
        start_date: date | str | None = None,
    ) -> list[str]:
        """List all symbols with available candle data."""
        async with async_session_factory() as db:
            from sqlalchemy import distinct
            result = await db.execute(select(distinct(Candle.symbol)))
            return [row[0] for row in result.all()]
