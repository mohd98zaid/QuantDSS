"""Pydantic schemas for Backtest endpoints."""
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel


class BacktestCreate(BaseModel):
    strategy_id: int
    symbol_id: int
    timeframe: str | None = "1d"
    start_date: date | None = None
    end_date: date | None = None
    initial_capital: float = 100000.0
    slippage_pct: float = 0.0005
    params_override: dict[str, Any] | None = None


class BacktestResponse(BaseModel):
    id: int
    strategy_name: str | None = None
    symbol: str | None = None
    timeframe: str
    start_date: str
    end_date: str
    initial_capital: float
    final_capital: float
    total_return_pct: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    max_drawdown_pct: float
    sharpe_ratio: float
    profit_factor: float

    model_config = {"from_attributes": True}


class BacktestTradeResponse(BaseModel):
    id: int
    entry_time: datetime | None = None
    exit_time: datetime | None = None
    entry_price: float | None = None
    exit_price: float | None = None
    quantity: int | None = None
    direction: str | None = None
    gross_pnl: float | None = None
    costs: float | None = None
    net_pnl: float | None = None
    exit_reason: str | None = None

    model_config = {"from_attributes": True}
