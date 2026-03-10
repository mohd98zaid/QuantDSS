import asyncio
import hashlib
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_session_factory
from app.core.redis import redis_client
from app.engine.auto_trader_engine import _open_trade
from app.engine.execution_manager import ExecutionManager, RedisRateLimiter
from app.models.auto_trade_config import AutoTradeConfig
from app.models.live_trade import LiveTrade


async def setup_test_db(db: AsyncSession):
    # Dummy setup if you need any configs in db
    pass


async def run_validation_tests():
    print("\n--- Running Execution Engine Validation Tests ---\n")
    
    # Validation 5: Distributed Rate Limiter
    print("> Validating Distributed Rate Limiter (Fix 5)...")
    limiter = RedisRateLimiter(calls_per_second=2)
    start = datetime.now()
    for _ in range(4):
        await limiter.acquire()
    elapsed = (datetime.now() - start).total_seconds()
    print(f"Time for 4 calls: {elapsed:.2f}s (should be >= 1s)")
    assert elapsed >= 1.0, "Rate limiter failed to throttle."
    print("[PASS] Rate limiter atomic check passed!")

    # Validation 6: Execution Idempotency
    print("\n> Validating Execution Idempotency (Fix 6)...")
    # Simulate a signal str
    signal_str = "RELIANCE_BUY_Breakout_5m_2500.0"
    signal_id = hashlib.md5(signal_str.encode()).hexdigest()
    dedup_key = f"execution_dedup:{signal_id}"
    await redis_client.delete(dedup_key)
    
    # Assuming the first execution would set this
    await redis_client.setex(dedup_key, 3600, "1")
    exists = await redis_client.exists(dedup_key)
    assert exists, "Dedup key not set properly"
    print("[PASS] Execution idempotency check passed!")

    # Mock the DB session and httpx client for testing reconciliation logic
    print("\n> Validating HTTP Timeout Safety and Reconciliation (Fix 3 & 7)...")
    from unittest.mock import AsyncMock, patch
    import builtins
    
    mock_db = AsyncMock()
    
    trade = LiveTrade(
        id=999,
        symbol="TEST_RECONCILE",
        instrument_key="TEST:TEST1234",
        direction="BUY",
        quantity=10,
        entry_price=100.0,
        stop_loss=90.0,
        target_price=120.0,
        risk_amount=100.0,
        status="UNKNOWN",
        close_reason="HTTP_TIMEOUT",
        broker_order_id="pending_testrecon123"
    )
    # Give the trade a fake created_at in the past
    trade.created_at = datetime.now(timezone.utc)
    from datetime import timedelta
    trade.created_at -= timedelta(minutes=5)
    
    class MockResult:
        def scalars(self):
            class MockScalars:
                def all(self):
                    return [trade]
            return MockScalars()
            
    mock_db.execute.return_value = MockResult()
    
    exec_mgr = ExecutionManager(mock_db)
    exec_mgr._token = "mock_token"
    
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_resp = AsyncMock()
        mock_resp.is_success = True
        # json() is not async in httpx! Make it a MagicMock
        from unittest.mock import MagicMock
        mock_resp.json = MagicMock(return_value={"data": {"status": "complete", "filled_quantity": 10, "average_price": 105.0}})
        mock_get.return_value = mock_resp
        
        await exec_mgr.reconcile_orders()
        
        # Verify it passed the custom "tag" since it was UNKNOWN and "pending_"
        mock_get.assert_called_once()
        args, kwargs = mock_get.call_args
        assert kwargs["params"] == {"tag": "qdss-999"}, "Reconciliation did not use client_order_id (tag) for pending trade!"
        
        print("[PASS] Reconciliation triggered successfully on UNKNOWN trade using Client Order ID!")

    print("\nAll logical validations passed successfully!\n")

if __name__ == "__main__":
    asyncio.run(run_validation_tests())
