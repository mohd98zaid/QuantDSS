"""
Scanner Worker — Standalone service.

Runs a continuous background loop to scan all stocks in PRESET_LISTS
using the `scanner.py` router logic. If signals are found, it publishes them
to `signals:candidate` via `_auto_trade_hook`.

Run:
    python -m app.workers.scanner_worker
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone

from app.api.routers.scanner import PRESET_LISTS, _scan_one, _auto_trade_hook
from app.core.logging import logger
from app.workers.base import WorkerBase

# Environment variable to control the scanner polling interval in seconds
# Default: 300 seconds (5 minutes)
SCAN_INTERVAL_SECONDS = int(os.getenv("SCANNER_INTERVAL", "300"))
# Which lists to automatically scan. Comma separated. Default: "nifty50"
ACTIVE_LISTS = os.getenv("SCANNER_ACTIVE_LISTS", "nifty50").split(",")


class ScannerWorker(WorkerBase):
    """
    Periodically scans stocks to find signals, completely independent of the frontend UI.
    """

    NAME = "scanner-worker"

    def __init__(self):
        super().__init__()
        self._strategy = "ema_crossover" # Configurable via DB later if needed
        self._timeframe = "5min"
        self._sem = asyncio.Semaphore(5) # limit concurrency
        
    async def _scan_list(self, list_name: str):
        symbols = PRESET_LISTS.get(list_name)
        if not symbols:
            logger.warning(f"[{self.NAME}] Unknown preset list: {list_name}")
            return
            
        logger.info(f"[{self.NAME}] Starting scan for {len(symbols)} stocks in {list_name}")
        
        async def _bounded(sym: str):
            async with self._sem:
                return await _scan_one(sym, self._strategy, self._timeframe)

        results = list(await asyncio.gather(*[_bounded(s) for s in symbols]))
        signals = [r for r in results if r.signal in ("BUY", "SELL")]
        
        logger.info(f"[{self.NAME}] Finished {list_name}. Found {len(signals)} signals out of {len(results)}.")

        if signals:
            try:
                await _auto_trade_hook(signals, self._strategy, self._timeframe)
                logger.info(f"[{self.NAME}] Sent {len(signals)} signals from {list_name} to AutoTrader hook")
            except Exception as e:
                logger.exception(f"[{self.NAME}] Failed to execute auto_trade_hook for {list_name}: {e}")

    async def _get_db_config(self) -> tuple[int, list[str], str, str]:
        from app.core.database import async_session_factory
        from app.models.auto_trade_config import AutoTradeConfig
        from sqlalchemy import select
        
        interval = SCAN_INTERVAL_SECONDS
        active_lists = ACTIVE_LISTS
        strategy = "ema_crossover"
        timeframe = "5min"
        
        try:
            async with async_session_factory() as db:
                cfg = (await db.execute(select(AutoTradeConfig))).scalar_one_or_none()
                if cfg:
                    if cfg.scan_interval_minutes > 0:
                        interval = cfg.scan_interval_minutes * 60
                    if getattr(cfg, "watchlist", None):
                        active_lists = cfg.watchlist
                    if getattr(cfg, "strategy", None):
                        strategy = cfg.strategy
                    if getattr(cfg, "timeframe", None):
                        timeframe = cfg.timeframe
        except Exception as e:
            logger.warning(f"[{self.NAME}] Failed to fetch config from DB, using fallback: {e}")
            
        return interval, active_lists, strategy, timeframe

    async def run(self):
        logger.info(f"[{self.NAME}] Starting background scanner loop")
        
        # Wait a moment for the DB and other workers to initialize
        await asyncio.sleep(5)
        
        while self.is_running:
            try:
                interval, active_lists, strategy, timeframe = await self._get_db_config()
                self._strategy = strategy
                self._timeframe = timeframe
                
                logger.info(f"[{self.NAME}] Scanner wake-up. Scanning lists: {active_lists} with {strategy} ({timeframe})")
                for lst in active_lists:
                    lst = lst.strip()
                    if lst:
                        await self._scan_list(lst)
            except Exception as e:
                logger.exception(f"[{self.NAME}] Scanner cycle error: {e}")
                
            # Sleep in 1-second increments so shutdown is responsive
            logger.debug(f"[{self.NAME}] Next scan in {interval} seconds")
            for _ in range(interval):
                if not self.is_running:
                    break
                await asyncio.sleep(1)

if __name__ == "__main__":
    ScannerWorker().main()
