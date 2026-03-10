"""
Tick Data Lake Storage

Stores raw market ticks to TimescaleDB for research, backtesting, and debugging.
"""
from sqlalchemy import text

from app.core.database import async_session_factory
from app.core.logging import logger
from app.ingestion.broker_adapter import NormalisedTick


async def write_tick(tick: NormalisedTick) -> None:
    """
    Writes a NormalisedTick to the ticks hypertable asynchronously.
    This guarantees long-term retention of full tick granularity.
    """
    try:
        async with async_session_factory() as db:
            await db.execute(
                text("""
                    INSERT INTO ticks (symbol, price, volume, exchange_timestamp)
                    VALUES (:symbol, :price, :volume, :exchange_timestamp)
                """),
                {
                    "symbol": tick.symbol,
                    "price": tick.ltp,
                    "volume": tick.volume,
                    "exchange_timestamp": tick.timestamp
                }
            )
            await db.commit()
    except Exception as e:
        logger.error(f"[TickStorage] Failed to write tick for {tick.symbol} to storage: {e}")
