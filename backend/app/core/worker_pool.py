"""
Worker Pool — singleton ProcessPoolExecutor for CPU-heavy tasks.

Issue 8b Fix:
  Indicator calculations (pandas rolling windows, TA-Lib) are CPU-bound.
  Running them synchronously inside the asyncio event loop blocks WebSocket
  message processing during peak market-open periods.

  Solution: wrap indicator computation with
      await loop.run_in_executor(get_worker_pool(), compute_fn, df)
  so that the calculation runs in a separate OS process and the event loop
  remains free to handle incoming ticks.

Usage in signal_pipeline.py:
    from app.core.worker_pool import get_worker_pool
    import asyncio

    loop = asyncio.get_event_loop()
    df_with_indicators = await loop.run_in_executor(
        get_worker_pool(),
        indicator_engine.compute_indicators_sync,
        df_raw,
    )

Important: functions passed to ProcessPoolExecutor must be top-level and
pickle-able (no lambdas, no bound methods of non-pickleable objects).
"""
import os
from concurrent.futures import ProcessPoolExecutor
from app.core.logging import logger

_pool: ProcessPoolExecutor | None = None


def get_worker_pool() -> ProcessPoolExecutor:
    """Return (or create) the shared ProcessPoolExecutor."""
    global _pool
    if _pool is None:
        workers = min(4, (os.cpu_count() or 2))
        _pool = ProcessPoolExecutor(max_workers=workers)
        logger.info(f"WorkerPool: started with {workers} process(es)")
    return _pool


def shutdown_worker_pool() -> None:
    """Gracefully shut down the process pool. Call from lifespan shutdown."""
    global _pool
    if _pool is not None:
        _pool.shutdown(wait=False, cancel_futures=True)
        _pool = None
        logger.info("WorkerPool: shut down")
