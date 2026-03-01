"""Pytest conftest — Test configuration and fixtures."""
from dataclasses import dataclass
from datetime import datetime

import pytest


@dataclass
class FakeRiskConfig:
    """Minimal risk config for unit tests."""
    risk_per_trade_pct: float = 0.01
    max_daily_loss_inr: float = 500.0
    max_daily_loss_pct: float = 0.02
    max_account_drawdown_pct: float = 0.10
    cooldown_minutes: int = 15
    min_atr_pct: float = 0.003
    max_atr_pct: float = 0.030
    max_position_pct: float = 0.20
    max_concurrent_positions: int = 2


@dataclass
class FakeDailyRiskState:
    """Minimal daily risk state for unit tests."""
    realised_pnl: float = 0.0
    last_signal_time: datetime | None = None
    is_halted: bool = False
    halt_reason: str | None = None
    halt_triggered_at: datetime | None = None
    signals_approved: int = 0
    signals_blocked: int = 0
    signals_skipped: int = 0


@pytest.fixture
def risk_config():
    return FakeRiskConfig()


@pytest.fixture
def daily_state():
    return FakeDailyRiskState()
