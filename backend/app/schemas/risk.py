"""Pydantic schemas for Risk endpoints."""
from datetime import date, datetime

from pydantic import BaseModel


class RiskConfigResponse(BaseModel):
    risk_per_trade_pct: float
    max_daily_loss_inr: float
    max_daily_loss_pct: float
    max_account_drawdown_pct: float
    cooldown_minutes: int
    min_atr_pct: float
    max_atr_pct: float
    max_position_pct: float
    max_concurrent_positions: int
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}


class RiskConfigUpdate(BaseModel):
    risk_per_trade_pct: float | None = None
    max_daily_loss_inr: float | None = None
    max_daily_loss_pct: float | None = None
    max_account_drawdown_pct: float | None = None
    cooldown_minutes: int | None = None
    min_atr_pct: float | None = None
    max_atr_pct: float | None = None
    max_position_pct: float | None = None
    max_concurrent_positions: int | None = None


class RiskStateResponse(BaseModel):
    trade_date: date
    account_balance: float | None = None
    max_daily_loss: float | None = None
    realised_pnl: float
    remaining_daily_buffer: float | None = None
    trades_taken: int
    signals_approved: int
    signals_blocked: int
    signals_skipped: int
    is_halted: bool
    halt_reason: str | None = None
    cooldown_active: bool = False
    cooldown_remaining_seconds: int = 0
    open_positions: int = 0
    max_concurrent_positions: int = 2
