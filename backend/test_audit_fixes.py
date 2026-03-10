import asyncio
import sys
import os

# Ensure the app can be imported
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../backend")))

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.database import async_session_factory
from app.models.risk_config import RiskConfig
from app.models.auto_trade_config import AutoTradeConfig
from app.core.streams import publish_to_stream
from app.workers.autotrader_worker import AutoTraderWorker
from app.models.paper_trade import PaperTrade
from app.models.live_trade import LiveTrade

async def reset_state(db: AsyncSession):
    # Reset balance and open positions for clean test run
    cfg = (await db.execute(select(RiskConfig).limit(1))).scalar_one()
    cfg.paper_balance = 100000.0  # Reset to 1L
    
    trade_cfg = (await db.execute(select(AutoTradeConfig).limit(1))).scalar_one()
    trade_cfg.max_open_positions = 5
    trade_cfg.capital_per_trade = 10000.0 # 10k per trade -> qty=100 for price 100
    
    # close all trades
    from sqlalchemy import update
    await db.execute(update(PaperTrade).where(PaperTrade.status == "OPEN").values(status="CLOSED"))
    await db.execute(update(LiveTrade).where(LiveTrade.status == "OPEN").values(status="CLOSED"))
    
    await db.commit()
    return cfg.paper_balance

async def test_race_condition_and_max_positions():
    async with async_session_factory() as db:
        initial_balance = await reset_state(db)
        print(f"Initial Balance: {initial_balance}")

    # Generate 10 concurrent signals (but max open is 5)
    worker = AutoTraderWorker()
    
    coros = []
    for i in range(10):
        signal_data = {
            "symbol_name": f"TEST_SYM_{i}",
            "signal_type": "BUY",
            "entry_price": "100.0", # 100.0 * 100 qty = 10000 notional. margin = 2000
            "stop_loss": "90.0",
            "target_price": "120.0",
            "quantity": "0", # let it calculate based on capital
            "_trace_id": f"trace_{i}",
            "symbol_id": "1",
            "is_replay": "true" # Bypass market hours check
        }
        coros.append(worker._handle_signal(f"msg_{i}", signal_data))
        
    print("Testing 10 concurrent signals execution...")
    await asyncio.gather(*coros)

    async with async_session_factory() as db:
        final_balance = (await db.execute(select(RiskConfig).limit(1))).scalar_one().paper_balance
        
        from sqlalchemy import func
        open_count = (await db.execute(select(func.count()).select_from(PaperTrade).where(PaperTrade.status == "OPEN"))).scalar_one()
        
        print(f"Final Balance: {final_balance}")
        print(f"Open Positions: {open_count}")
        
        # Expected:
        # Max open positions = 5. Therefore only 5 trades should succeed.
        # Each trade uses 10_000 capital -> 10_000 / 100 = 100 qty.
        # Margin per trade = (100 * 100) / 5 = 2000.
        # 5 trades * 2000 margin = 10_000 total margin deducted.
        # Expected final balance = 100_000 - 10_000 = 90_000.0
        
        if open_count == 5 and final_balance == 90000.0:
            print("✅ TEST 1 & 2 PASSED: Race Condition mitigated and Max Positions Enforced.")
        else:
            print(f"❌ TEST FAILED: expected count 5, balance 90000. Got count {open_count}, balance {final_balance}")

async def test_entry_price_zero():
    worker = AutoTraderWorker()
    signal_data = {
        "symbol_name": "TEST_ZERO",
        "signal_type": "BUY",
        "entry_price": "0.0", 
        "stop_loss": "0.0",
        "target_price": "0.0",
        "quantity": "0", 
        "_trace_id": f"trace_zero",
        "symbol_id": "1",
        "is_replay": "true"
    }
    print("\nTesting entry price 0...")
    await worker._handle_signal(f"msg_zero", signal_data)
    
    async with async_session_factory() as db:
        from sqlalchemy import select
        trade = (await db.execute(select(PaperTrade).where(PaperTrade.symbol == "TEST_ZERO"))).scalar_one_or_none()
        if trade is None:
             print("✅ TEST 3 PASSED: Zero entry price gracefully rejected.")
        else:
             print("❌ TEST 3 FAILED: Trade created for zero entry price.")

async def main():
    await test_race_condition_and_max_positions()
    await test_entry_price_zero()

if __name__ == "__main__":
    asyncio.run(main())
