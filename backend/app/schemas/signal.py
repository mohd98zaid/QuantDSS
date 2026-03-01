"""Pydantic schemas for Signal endpoints."""
from datetime import datetime

from pydantic import BaseModel


class SignalResponse(BaseModel):
    id: int
    timestamp: datetime | None = None
    symbol: str | None = None
    strategy: str | None = None
    signal_type: str
    entry_price: float | None = None
    stop_loss: float | None = None
    target_price: float | None = None
    quantity: int | None = None
    risk_amount: float | None = None
    risk_pct: float | None = None
    risk_reward: float | None = None
    risk_status: str
    block_reason: str | None = None
    atr_pct: float | None = None

    model_config = {"from_attributes": True}


class SignalListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    signals: list[SignalResponse]
