"""
Redis Lock — Distributed Context Manager

Provides an asynchronous context manager for distributed locking via Redis.
Used to prevent race conditions across distributed workers.
"""
from contextlib import asynccontextmanager
import asyncio
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
    acquired = False

    try:
        # Spinlock loop
        start_time = asyncio.get_event_loop().time()
        while True:
            # SET NX (Not eXists) EX (EXpire in seconds)
            acquired = await redis.set(lock_key, "locked", nx=True, ex=timeout)
            if acquired:
                break
            
            if asyncio.get_event_loop().time() - start_time > timeout:
                raise TimeoutError(f"Failed to acquire Redis lock '{lock_name}' within {timeout}s")
                
            await asyncio.sleep(retry_delay)

        yield True

    finally:
        if acquired:
            # Safely release lock (though expiry acts as fallback)
            await redis.delete(lock_key)
