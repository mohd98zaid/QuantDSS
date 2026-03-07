import asyncio
from sqlalchemy import text
from app.core.database import async_session_factory

async def check_signals_breakdown():
    async with async_session_factory() as db:
        res = await db.execute(text("SELECT risk_status, count(*) FROM signals GROUP BY risk_status"))
        breakdown = res.all()
        
        print("--- Signal Risk Status Breakdown ---")
        for row in breakdown:
            print(f"Status: {row[0]} | Count: {row[1]}")
            
        # Also check rejection reasons for some blocked ones
        res_reasons = await db.execute(text("SELECT block_reason, count(*) FROM signals WHERE risk_status != 'APPROVED' GROUP BY block_reason"))
        reasons = res_reasons.all()
        print("\n--- Rejection Reasons ---")
        for row in reasons:
            print(f"Reason: {row[0]} | Count: {row[1]}")

if __name__ == "__main__":
    asyncio.run(check_signals_breakdown())
