import asyncio
import time
from app.core.redis import redis_client

async def main():
    print("Generating 50,000 keys...")
    pipe = redis_client.pipeline()
    for i in range(50000):
        pipe.set(f"risk_reservation:test:{i}", "data")
        if i % 10000 == 0:
            await pipe.execute()
    await pipe.execute()
    
    print("Testing KEYS latency...")
    t0 = time.time()
    keys1 = await redis_client.keys("risk_reservation:*")
    t1 = time.time()
    keys_time = t1 - t0
    print(f"KEYS took {keys_time:.4f} seconds ({len(keys1)} keys)")
    
    print("Testing SCAN latency...")
    t0 = time.time()
    cursor = 0
    keys2 = []
    while True:
        cursor, batch = await redis_client.scan(cursor=cursor, match="risk_reservation:*", count=1000)
        keys2.extend(batch)
        if cursor == 0 or cursor == b'0':
            break
    t1 = time.time()
    scan_time = t1 - t0
    print(f"SCAN took {scan_time:.4f} seconds ({len(keys2)} keys)")
    
    print("Cleaning up...")
    cursor = 0
    while True:
        cursor, batch = await redis_client.scan(cursor=cursor, match="risk_reservation:test:*", count=5000)
        if batch:
            await redis_client.delete(*batch)
        if cursor == 0 or cursor == b'0':
            break

if __name__ == "__main__":
    asyncio.run(main())
