"""Candles router — OHLCV market data."""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_session
from app.models.candle import Candle
from app.models.symbol import Symbol
from app.schemas.candle import CandleResponse, CandlesResponse

router = APIRouter()


@router.get("/candles/{symbol}/{timeframe}", response_model=CandlesResponse)
async def get_candles(
    symbol: str,
    timeframe: str = "1min",
    limit: int = Query(100, ge=1, le=1000),
    from_time: datetime | None = Query(None, alias="from"),
    to_time: datetime | None = Query(None, alias="to"),
    db: AsyncSession = Depends(get_session),
    _user: dict = Depends(get_current_user),
):
    """Fetch OHLCV candles for a symbol."""
    # Find the symbol
    sym_result = await db.execute(
        select(Symbol).where(Symbol.trading_symbol == symbol.upper())
    )
    sym = sym_result.scalar_one_or_none()
    if not sym:
        raise HTTPException(status_code=404, detail=f"Symbol {symbol} not found")

    # Build query
    query = (
        select(Candle)
        .where(
            and_(
                Candle.symbol_id == sym.id,
                Candle.timeframe == timeframe,
            )
        )
        .order_by(Candle.time.desc())
        .limit(limit)
    )

    if from_time:
        query = query.where(Candle.time >= from_time)
    if to_time:
        query = query.where(Candle.time <= to_time)

    result = await db.execute(query)
    candles = result.scalars().all()

    return CandlesResponse(
        symbol=symbol.upper(),
        timeframe=timeframe,
        candles=[
            CandleResponse(
                time=c.time,
                open=float(c.open),
                high=float(c.high),
                low=float(c.low),
                close=float(c.close),
                volume=c.volume,
            )
            for c in reversed(candles)  # Return in chronological order
        ],
    )
