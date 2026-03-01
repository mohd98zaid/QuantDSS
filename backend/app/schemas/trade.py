"""Pydantic schemas for Trade Journal endpoints."""
from datetime import date, datetime

from pydantic import BaseModel


class TradeCreate(BaseModel):
    signal_id: int | None = None
    symbol_id: int
    direction: str
    quantity: int
    entry_price: float
    exit_price: float | None = None
    entry_time: datetime | None = None
    exit_time: datetime | None = None
    exit_reason: str | None = None
    brokerage: float | None = None
    notes: str | None = None


class TradeResponse(BaseModel):
    id: int
    signal_id: int | None = None
    symbol: str | None = None
    trade_date: date
    direction: str
    entry_price: float | None = None
    exit_price: float | None = None
    quantity: int | None = None
    gross_pnl: float | None = None
    brokerage: float | None = None
    net_pnl: float | None = None
    exit_reason: str | None = None
    entry_time: datetime | None = None
    exit_time: datetime | None = None

    model_config = {"from_attributes": True}


class TradeListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    trades: list[TradeResponse]


class TradeSummary(BaseModel):
    period: str
    total_trades: int
    winners: int
    losers: int
    net_pnl: float
    win_rate: float
    avg_win: float
    avg_loss: float
