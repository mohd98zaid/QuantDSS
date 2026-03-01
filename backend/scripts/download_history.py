"""
Bulk historical data download script.
Downloads 5 years of daily OHLCV data for all active symbols via yfinance.
Run: python -m scripts.download_history
"""
import asyncio
from datetime import UTC
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.logging import logger
from app.ingestion.adapters.yfinance_adapter import YFinanceAdapter
from app.models.candle import Candle
from app.models.symbol import Symbol


async def download_and_store():
    """Download historical data for all active symbols and store in TimescaleDB."""
    engine = create_async_engine(settings.database_url, echo=False)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        # Get all active symbols
        result = await session.execute(
            select(Symbol).where(Symbol.is_active is True)
        )
        symbols = result.scalars().all()

        if not symbols:
            print("❌ No active symbols found. Run seed script first.")
            return

        print(f"📊 Found {len(symbols)} active symbols. Starting download...")

        adapter = YFinanceAdapter()
        total_candles = 0

        for sym in symbols:
            print(f"\n⏳ Downloading {sym.trading_symbol}...")

            data = adapter.download_history(
                symbol=sym.trading_symbol,
                period="5y",
                interval="1d",
            )

            if data is None or data.empty:
                print(f"  ⚠️  No data for {sym.trading_symbol}")
                continue

            # Insert candles into DB
            count = 0
            for idx, row in data.iterrows():
                try:
                    # Handle both timezone-aware and naive timestamps
                    if hasattr(idx, 'tz') and idx.tz:
                        candle_time = idx.to_pydatetime()
                    else:
                        candle_time = idx.to_pydatetime().replace(tzinfo=UTC)

                    candle = Candle(
                        time=candle_time,
                        symbol_id=sym.id,
                        timeframe="1d",
                        open=Decimal(str(round(float(row["Open"]), 2))),
                        high=Decimal(str(round(float(row["High"]), 2))),
                        low=Decimal(str(round(float(row["Low"]), 2))),
                        close=Decimal(str(round(float(row["Close"]), 2))),
                        volume=int(row["Volume"]),
                    )
                    session.add(candle)
                    count += 1
                except Exception as e:
                    logger.debug(f"  Skipping row {idx}: {e}")
                    continue

            try:
                await session.commit()
                total_candles += count
                print(f"  ✅ {sym.trading_symbol}: {count} candles stored")
            except Exception as e:
                await session.rollback()
                print(f"  ⚠️  {sym.trading_symbol}: Error storing data — {e}")
                # Try inserting one-by-one for duplicate handling
                for idx, row in data.iterrows():
                    try:
                        if hasattr(idx, 'tz') and idx.tz:
                            candle_time = idx.to_pydatetime()
                        else:
                            candle_time = idx.to_pydatetime().replace(tzinfo=UTC)

                        candle = Candle(
                            time=candle_time,
                            symbol_id=sym.id,
                            timeframe="1d",
                            open=Decimal(str(round(float(row["Open"]), 2))),
                            high=Decimal(str(round(float(row["High"]), 2))),
                            low=Decimal(str(round(float(row["Low"]), 2))),
                            close=Decimal(str(round(float(row["Close"]), 2))),
                            volume=int(row["Volume"]),
                        )
                        session.add(candle)
                        await session.commit()
                    except Exception:
                        await session.rollback()
                        continue

        print(f"\n🎉 Download complete! Total candles stored: {total_candles}")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(download_and_store())
