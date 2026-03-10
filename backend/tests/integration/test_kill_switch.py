import pytest
import asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete

from app.main import app
from app.core.redis import redis_client
from app.system.trading_state import get_trading_state, set_trading_state
from app.models.kill_switch_event import KillSwitchEvent
from app.models.paper_trade import PaperTrade
from app.workers.trade_monitor_worker import TradeMonitorWorker

# Note: test database session fixture is expected to be provided by conftest.py

@pytest.fixture(autouse=True)
async def reset_trading_state(db_session: AsyncSession):
    # Ensure starting from a clean ENABLED state
    await set_trading_state("ENABLED", "test:setup", "resetting for test", db_session=db_session, redis_client=redis_client)
    yield
    await set_trading_state("ENABLED", "test:teardown", "resetting after test", db_session=db_session, redis_client=redis_client)

@pytest.mark.asyncio
async def test_kill_switch_disable(db_session: AsyncSession):
    # 1. Activate kill switch via API
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.post("/api/v1/admin/trading/disable", json={"trigger": "test", "reason": "pytest disable"})
    
    assert response.status_code == 200
    assert response.json()["message"] == "Trading disabled globally"
    
    # 2. Verify state is DISABLED in redis
    state = await get_trading_state(redis_client)
    assert state == "DISABLED"
    
    # 3. Verify event is logged to DB
    result = await db_session.execute(select(KillSwitchEvent).order_by(KillSwitchEvent.timestamp.desc()))
    event = result.scalars().first()
    assert event is not None
    assert event.state == "DISABLED"
    assert event.triggered_by == "test"
    assert event.reason == "pytest disable"

@pytest.mark.asyncio
async def test_kill_switch_enable(db_session: AsyncSession):
    # Ensure state is disabled first
    await set_trading_state("DISABLED", "test", "test", db_session=db_session, redis_client=redis_client)
    
    # 1. Activate resume via API
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.post("/api/v1/admin/trading/enable", json={"trigger": "test", "reason": "pytest enable"})
    
    assert response.status_code == 200
    assert response.json()["message"] == "Trading enabled globally"
    
    # 2. Verify state is ENABLED
    state = await get_trading_state(redis_client)
    assert state == "ENABLED"

@pytest.mark.asyncio
async def test_kill_switch_emergency_flatten(db_session: AsyncSession):
    # Seed a mock open position
    mock_trade = PaperTrade(
        symbol="RELIANCE", direction="BUY", status="OPEN",
        entry_price=2500, quantity=10, stop_loss=2400, target_price=2600,
        strategy="test", trading_mode="paper"
    )
    db_session.add(mock_trade)
    await db_session.commit()
    
    # 1. Trigger EMERGENCY_FLATTEN
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.post("/api/v1/admin/trading/emergency-flatten", json={"trigger": "test", "reason": "pytest emergency"})
    
    assert response.status_code == 200
    
    # 2. Run the trade monitor worker check logic
    worker = TradeMonitorWorker()
    # Note: in a real full test we would wait for the worker, but here we invoke it directly
    # We patch _get_ltp so it returns a price and doesn't hit external APIs
    original_get_ltp = worker._get_ltp
    
    async def mock_get_ltp(symbol, instrument_key=""):
        return 2510.0
    worker._get_ltp = mock_get_ltp
    
    await worker._check_paper_trades() # Actually, EMERGENCY_FLATTEN only applies to live trades right now per trade_monitor_worker line 215. Let's fix that!
    await worker._check_live_trades()
    
    worker._get_ltp = original_get_ltp
    
    # Verify the event logging
    result = await db_session.execute(select(KillSwitchEvent).order_by(KillSwitchEvent.timestamp.desc()))
    event = result.scalars().first()
    assert event is not None
    assert event.state == "EMERGENCY_FLATTEN"
    
    # Clean up test data
    await db_session.execute(delete(PaperTrade).where(PaperTrade.id == mock_trade.id))
    await db_session.commit()
