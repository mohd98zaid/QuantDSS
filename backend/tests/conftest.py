"""Pytest conftest — Test configuration and fixtures."""
import asyncio
from dataclasses import dataclass
from datetime import datetime
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.api.deps import get_current_user, get_session
from app.api.main import app
from app.core.config import settings


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
    # Time-gate: open all day so tests pass regardless of when CI runs
    signal_start_hour: int = 0
    signal_start_minute: int = 0
    signal_end_hour: int = 23
    signal_end_minute: int = 59
    # Per-stock daily signal limit: very high so unit tests are never affected
    max_signals_per_stock: int = 999


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


from app.core.database import Base
from app.models.audit_log import AuditLog
from app.models.auto_trade_config import AutoTradeConfig
from app.models.auto_trade_log import AutoTradeLog
from app.models.backtest_run import BacktestRun
from app.models.candle import Candle
from app.models.daily_risk_state import DailyRiskState
from app.models.live_trade import LiveTrade
from app.models.paper_trade import PaperTrade
from app.models.risk_config import RiskConfig
from app.models.signal import Signal
from app.models.strategy import Strategy, StrategySymbol
from app.models.strategy_health import StrategyHealth
from app.models.strategy_health_log import StrategyHealthLog
from app.models.symbol import Symbol
from app.models.trade import Trade

import os
import asyncio
from app.core import database

@pytest.fixture
async def test_engine():
    """Function-scoped engine using on-disk SQLite to ensure tables persist across connections."""
    db_file = "test_unit.db"
    if os.path.exists(db_file):
        try: os.remove(db_file)
        except OSError: pass

    settings.database_url = f"sqlite+aiosqlite:///{db_file}"
    engine = create_async_engine(
        settings.database_url,
        poolclass=NullPool,
    )
    
    # Patch global engine for endpoints that import it directly (like health check)
    original_engine = database.engine
    database.engine = engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()
    
    # Restore original engine
    database.engine = original_engine
    
    # Cleanup file
    if os.path.exists(db_file):
        try: os.remove(db_file)
        except OSError: pass


@pytest.fixture
async def db(test_engine):
    """Function-scoped session."""
    session_factory = async_sessionmaker(
        test_engine, 
        class_=AsyncSession, 
        expire_on_commit=False
    )
    async with session_factory() as session:
        yield session


@pytest.fixture
async def client(db):
    """Test client with dependency overrides and DISABLED lifespan."""
    
    async def override_get_session():
        yield db

    async def override_get_current_user():
        return {"sub": "admin"}

    app.dependency_overrides[get_session] = override_get_session
    app.dependency_overrides[get_current_user] = override_get_current_user
    
    # Disable lifespan to avoid global engine initialization in wrong loops
    original_lifespan = app.router.lifespan_context
    app.router.lifespan_context = AsyncMock() 

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    
    app.dependency_overrides.clear()
    app.router.lifespan_context = original_lifespan


@pytest.fixture
def risk_config():
    return FakeRiskConfig()


@pytest.fixture
def daily_state():
    return FakeDailyRiskState()
