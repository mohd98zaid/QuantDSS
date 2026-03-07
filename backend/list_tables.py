import asyncio
from sqlalchemy import text
from app.core.database import async_session_factory

async def list_tables():
    async with async_session_factory() as db:
        result = await db.execute(text("SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname = 'public'"))
        tables = result.scalars().all()
        print("--- Tables in public schema ---")
        for t in tables:
            print(t)

if __name__ == "__main__":
    asyncio.run(list_tables())
