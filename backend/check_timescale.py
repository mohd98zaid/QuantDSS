import asyncio
import asyncpg
import os

async def check():
    db_url = os.getenv("DATABASE_URL", "postgresql+asyncpg://quant:quantdss@postgres:5432/quantdb")
    db_url = db_url.replace("postgresql+asyncpg://", "postgresql://")
    
    conn = await asyncpg.connect(db_url)
    
    print("=== HYPERTABLES ===")
    rows = await conn.fetch("SELECT hypertable_name FROM timescaledb_information.hypertables;")
    for row in rows:
        print(f"- {row['hypertable_name']}")
        
    print("\n=== COMPRESSION COMPLETED ===")
    try:
        rows2 = await conn.fetch("SELECT hypertable_name FROM timescaledb_information.compression_settings;")
        for row in rows2:
            print(f"- {row['hypertable_name']} configured for compression")
    except Exception as e:
        print("Error querying compression:", e)
        
    print("\n=== JOBS (Retention & Compression) ===")
    rows3 = await conn.fetch("SELECT job_id, application_name, schedule_interval, config FROM timescaledb_information.jobs;")
    for row in rows3:
        print(f"[{row['job_id']}] {row['application_name']} | Interval: {row['schedule_interval']} | Config: {row['config']}")

    await conn.close()

if __name__ == "__main__":
    asyncio.run(check())
