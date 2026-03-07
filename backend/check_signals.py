import asyncio
from sqlalchemy import select
from app.core.database import async_session_factory
from app.models.signal import Signal

async def check_signals():
    async with async_session_factory() as db:
        # Get last 10 signals
        result = await db.execute(select(Signal).order_by(Signal.created_at.desc()).limit(10))
        signals = result.scalars().all()
        
        print(f"--- Last {len(signals)} Signals ---")
        for s in signals:
            print(f"ID: {s.id} | Symbol: {s.symbol} | Type: {s.signal_type} | Status: {s.status} | Reason: {s.rejection_reason} | Time: {s.created_at}")

if __name__ == "__main__":
    asyncio.run(check_signals())
