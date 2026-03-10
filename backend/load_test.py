import asyncio
import json
import uuid
import sys
from datetime import datetime, timezone

import redis.asyncio as redis

async def run_load_test():
    r = redis.from_url("redis://:quantredis2025@redis:6379/0", decode_responses=True)
    stream_key = "signals:raw"
    count = 1000

    print(f"Injecting {count} signals to {stream_key}...")
    mem_before = await r.info('memory')
    print(f"Redis memory before: {mem_before['used_memory_human']}")
    
    # Batch pipeline to go faster
    pipe = r.pipeline()
    for i in range(count):
        signal = {
            "symbol_id": "1",
            "symbol_name": "RELIANCE",
            "strategy_id": "99",
            "signal_type": "BUY",
            "entry_price": "2500.0",
            "stop_loss": "2450.0",
            "target_price": "2600.0",
            "atr_value": "20.0",
            "candle_time": datetime.now(timezone.utc).isoformat(),
            "_trace_id": str(uuid.uuid4())
        }
        pipe.xadd(stream_key, signal)
        
        if i > 0 and i % 200 == 0:
            await pipe.execute()
    
    await pipe.execute() # remaining
    
    print("Done injecting.")
    await asyncio.sleep(2)
    
    mem_after = await r.info('memory')
    print(f"Redis memory after: {mem_after['used_memory_human']}")
    
    xlen = await r.xlen(stream_key)
    print(f"Stream length: {xlen}")

if __name__ == "__main__":
    asyncio.run(run_load_test())
