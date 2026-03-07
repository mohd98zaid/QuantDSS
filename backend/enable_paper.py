import asyncio
from sqlalchemy import update
from app.core.database import async_session_factory
from app.models.auto_trade_config import AutoTradeConfig

async def set_paper():
    async with async_session_factory() as db:
        await db.execute(update(AutoTradeConfig).values(enabled=True, mode="paper"))
        await db.commit()
    print("AutoTradeConfig set to enabled=True, mode='paper'.")

if __name__ == "__main__":
    asyncio.run(set_paper())
