"""
Worker Base — Shared bootstrap for all QuantDSS standalone workers.

Provides:
  - Database engine + session factory initialization
  - Redis client initialization
  - Signal handling (SIGTERM / SIGINT) for graceful shutdown
  - Common logging setup
  - run_worker() entry point that subclasses override

Usage:
    class MyWorker(WorkerBase):
        NAME = "my-worker"

        async def run(self):
            ...  # worker logic

    if __name__ == "__main__":
        MyWorker().main()
"""
from __future__ import annotations

import asyncio
import signal
import sys
from abc import ABC, abstractmethod

from app.core.config import settings
from app.core.logging import logger
from app.core.redis import redis_client


class WorkerBase(ABC):
    """
    Abstract base class for QuantDSS workers.

    Handles:
      - Graceful shutdown via SIGTERM/SIGINT
      - Database table verification at startup
      - Common lifecycle logging
    """

    NAME: str = "worker"

    def __init__(self):
        self._running = True
        self._shutdown_event = asyncio.Event()
        self._heartbeat_task = None

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def main(self):
        """Synchronous entry point — call from `if __name__ == '__main__':`."""
        try:
            asyncio.run(self._run_lifecycle())
        except KeyboardInterrupt:
            logger.info(f"[{self.NAME}] KeyboardInterrupt — exiting")

    async def _run_lifecycle(self):
        """Full async lifecycle: setup → run → teardown."""
        import uuid
        import structlog
        
        # Phase 1: Structured Logging Context Binding
        worker_id_str = str(uuid.uuid4())[:8]
        structlog.contextvars.bind_contextvars(
            service_name=self.NAME,
            worker_id=worker_id_str
        )
        
        from app.core.tracing import init_tracing
        init_tracing(service_name=self.NAME, worker_id=worker_id_str)
        
        # Register signal handlers
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, self._signal_handler)
            except NotImplementedError:
                # Windows doesn't support add_signal_handler
                pass

        logger.info(f"[{self.NAME}] Starting up...")

        # Ensure DB tables exist (same as main.py lifespan)
        await self._init_database()

        # Start heartbeat
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

        logger.info(f"[{self.NAME}] Initialization complete — entering main loop")

        try:
            await self.run()
        except asyncio.CancelledError:
            logger.info(f"[{self.NAME}] Cancelled")
        except Exception as e:
            logger.exception(f"[{self.NAME}] Fatal error: {e}")
            sys.exit(1)
        finally:
            if self._heartbeat_task:
                self._heartbeat_task.cancel()
            await self.teardown()
            logger.info(f"[{self.NAME}] Shut down gracefully")

    async def _heartbeat_loop(self):
        """Continuously publish worker heartbeat to Redis."""
        from app.core.metrics import WORKER_UPTIME
        import time
        start_time = time.time()
        
        while self.is_running:
            WORKER_UPTIME.labels(worker_name=self.NAME).set(time.time() - start_time)
            try:
                await redis_client.setex(f"worker_heartbeat:{self.NAME}", 10, "alive")
            except Exception as e:
                logger.debug(f"[{self.NAME}] Heartbeat failed: {e}")
            await asyncio.sleep(5)

    def _signal_handler(self):
        """Handle SIGTERM/SIGINT for graceful shutdown."""
        logger.info(f"[{self.NAME}] Received shutdown signal")
        self._running = False
        self._shutdown_event.set()

    @property
    def is_running(self) -> bool:
        return self._running

    def stop(self):
        """Programmatically request shutdown."""
        self._running = False
        self._shutdown_event.set()

    # ── Database ─────────────────────────────────────────────────────────────

    async def _init_database(self):
        """Ensure all DB tables exist — mirrors main.py startup."""
        from app.core.database import engine, Base

        # Import every model so SQLAlchemy registers them
        import app.models.symbol           # noqa: F401
        import app.models.strategy         # noqa: F401
        import app.models.signal           # noqa: F401
        import app.models.trade            # noqa: F401
        import app.models.paper_trade      # noqa: F401
        import app.models.risk_config      # noqa: F401
        import app.models.daily_risk_state # noqa: F401
        import app.models.audit_log        # noqa: F401
        import app.models.candle           # noqa: F401
        import app.models.backtest_run     # noqa: F401
        import app.models.auto_trade_config # noqa: F401
        import app.models.auto_trade_log   # noqa: F401

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        logger.info(f"[{self.NAME}] Database tables verified")

    # ── Subclass Interface ───────────────────────────────────────────────────

    @abstractmethod
    async def run(self):
        """Main worker logic — implement in subclass."""
        ...

    async def teardown(self):
        """Optional cleanup — override in subclass if needed."""
        pass
