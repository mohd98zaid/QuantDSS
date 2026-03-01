"""Pydantic schemas for Strategy endpoints."""
from datetime import datetime
from typing import Any

from pydantic import BaseModel


class StrategyResponse(BaseModel):
    id: int
    name: str
    type: str | None = None
    description: str | None = None
    is_active: bool
    parameters: dict[str, Any]
    min_backtest_trades: int
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}


class StrategyUpdate(BaseModel):
    is_active: bool | None = None
    parameters: dict[str, Any] | None = None


class StrategySymbolCreate(BaseModel):
    symbol_id: int
    timeframe: str = "1min"


class StrategySymbolResponse(BaseModel):
    id: int
    strategy_id: int
    symbol_id: int
    timeframe: str
    is_active: bool

    model_config = {"from_attributes": True}
