import asyncio
from sqlalchemy import text
from app.core.database import async_session_factory

async def check_stats():
    async with async_session_factory() as db:
        res_sig = await db.execute(text("SELECT count(*) FROM signals"))
        res_trade = await db.execute(text("SELECT count(*) FROM paper_trades"))
        res_config = await db.execute(text("SELECT enabled, mode FROM auto_trade_config LIMIT 1"))
        
        sig_count = res_sig.scalar()
        trade_count = res_trade.scalar()
        config = res_config.first()
        
        print(f"Signals in DB: {sig_count}")
        print(f"Paper Trades in DB: {trade_count}")
        if config:
            print(f"AutoTradeConfig: Enabled={config[0]}, Mode={config[1]}")
        else:
            print("AutoTradeConfig: NOT FOUND")

if __name__ == "__main__":
    asyncio.run(check_stats())
