"""Pydantic schemas for Symbol endpoints."""
from datetime import datetime

from pydantic import BaseModel


class SymbolCreate(BaseModel):
    trading_symbol: str
    exchange: str = "NSE"


class SymbolResponse(BaseModel):
    id: int
    trading_symbol: str
    exchange: str
    instrument_token: int | None = None
    is_active: bool
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class SymbolUpdate(BaseModel):
    instrument_token: int | None = None
    is_active: bool | None = None
