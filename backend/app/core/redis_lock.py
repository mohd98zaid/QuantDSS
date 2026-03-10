"""
Redis Lock — Distributed Context Manager

Provides an asynchronous context manager for distributed locking via Redis.
Used to prevent race conditions across distributed workers.
"""
from contextlib import asynccontextmanager
import asyncio
import uuid
from app.core.redis import get_redis


@asynccontextmanager
async def redis_lock(lock_name: str, timeout: int = 10, retry_delay: float = 0.1):
    """
    Acquire a distributed lock via Redis.
    Yields True if acquired, raises TimeoutError if unable to acquire.
    Auto-releases the lock upon exiting context.
    """
    redis = await get_redis()
    lock_key = f"lock:{lock_name}"
    nonce = str(uuid.uuid4())
    acquired = False

    try:
        # Spinlock loop
        start_time = asyncio.get_event_loop().time()
        while True:
            # SET NX (Not eXists) EX (EXpire in seconds)
            acquired = await redis.set(lock_key, nonce, nx=True, ex=timeout)
            if acquired:
                break
            
            if asyncio.get_event_loop().time() - start_time > timeout:
                raise TimeoutError(f"Failed to acquire Redis lock '{lock_name}' within {timeout}s")
                
            await asyncio.sleep(retry_delay)

        yield True

    finally:
        if acquired:
            # Fix Group 4: Safe lock release via Lua script
            script = """
            if redis.call("get", KEYS[1]) == ARGV[1] then
                return redis.call("del", KEYS[1])
            else
                return 0
            end
            """
            await redis.eval(script, 1, lock_key, nonce)
