"""Pydantic schemas for Candle endpoints."""
from datetime import datetime

from pydantic import BaseModel


class CandleResponse(BaseModel):
    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int

    model_config = {"from_attributes": True}


class CandlesResponse(BaseModel):
    symbol: str
    timeframe: str
    candles: list[CandleResponse]
