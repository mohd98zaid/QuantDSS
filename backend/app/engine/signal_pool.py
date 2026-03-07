"""
Candidate Signal Pool Service — Phase 2 of Signal Intelligence System.

Temporarily stores CandidateSignals, groups them by symbol, and handles the
time-window buffering before forwarding to the Consolidation Layer.
"""

import asyncio
from datetime import datetime, UTC
from collections import defaultdict
from typing import Dict, List, Callable, Awaitable

import json
from dataclasses import asdict

from app.core.logging import logger
from app.core.redis import redis_client
from app.engine.base_strategy import CandidateSignal

def _serialize_signal(sig: CandidateSignal) -> str:
    d = asdict(sig)
    if 'candle_time' in d and isinstance(d['candle_time'], datetime):
        d['candle_time'] = d['candle_time'].isoformat()
    return json.dumps(d)

def _deserialize_signal(data: str) -> CandidateSignal:
    d = json.loads(data)
    if 'candle_time' in d and isinstance(d['candle_time'], str):
        d['candle_time'] = datetime.fromisoformat(d['candle_time'])
    return CandidateSignal(**d)


class CandidateSignalPool:
    """
    In-memory pool that collects CandidateSignals from strategies.
    It groups signals by symbol and maintains a short time window.
    Signals are automatically flushed (forwarded) to the Consolidation Layer
    once they age out of the time window.
    """

    def __init__(self, window_seconds: int = 2):
        self.window_seconds = window_seconds
        self._redis_key_prefix = "signal_pool:"
        
        # Structure: map[symbol_id] -> map[strategy_name] -> CandidateSignal
        self._pool: defaultdict[int, Dict[str, CandidateSignal]] = defaultdict(dict)
        
        # Track the first recorded timestamp of the current symbol's signal cluster
        self._first_signal_time: Dict[int, datetime] = {}
        
        self._lock = asyncio.Lock()
        self._flush_task: asyncio.Task | None = None
        self._consolidation_callback: Callable[[List[CandidateSignal]], Awaitable[None]] | None = None

    def set_callback(self, callback: Callable[[List[CandidateSignal]], Awaitable[None]]):
        """Set the async callback for when signals are flushed (usually Consolidation Layer)."""
        self._consolidation_callback = callback

    def start(self):
        """Start the background flush task."""
        if not self._flush_task:
            self._flush_task = asyncio.create_task(self._run())
            logger.info("CandidateSignalPool background flush task started.")

    async def _run(self):
        """Recover from Redis then start the flush loop."""
        await self._recover_from_redis()
        await self._flush_loop()

    async def _recover_from_redis(self):
        """Recover pending signals from Redis after a restart."""
        try:
            keys = await redis_client.keys(f"{self._redis_key_prefix}*")
            if not keys:
                return

            now = datetime.now(UTC)
            restored_count = 0
            for key in keys:
                symbol_id_str = key.decode('utf-8') if isinstance(key, bytes) else key
                symbol_id_str = symbol_id_str.split(':')[-1]
                symbol_id = int(symbol_id_str)

                hash_data = await redis_client.hgetall(key)
                if not hash_data:
                    continue

                async with self._lock:
                    if symbol_id not in self._first_signal_time:
                        self._first_signal_time[symbol_id] = now

                    for strat_name, sig_data in hash_data.items():
                        strat_name_str = strat_name.decode('utf-8') if isinstance(strat_name, bytes) else strat_name
                        try:
                            sig_json = sig_data.decode('utf-8') if isinstance(sig_data, bytes) else sig_data
                            sig = _deserialize_signal(sig_json)
                            self._pool[symbol_id][strat_name_str] = sig
                            restored_count += 1
                        except Exception as e:
                            logger.error(f"Error deserializing recovered signal: {e}")

            if restored_count > 0:
                logger.info(f"CandidateSignalPool recovered {restored_count} signals from Redis.")
        except Exception as e:
            logger.error(f"Error recovering signals from Redis: {e}")

    def stop(self):
        """Stop the background flush task."""
        if self._flush_task is not None:
            self._flush_task.cancel()
            self._flush_task = None
            logger.info("CandidateSignalPool background flush task stopped.")

    async def add_signal(self, signal: CandidateSignal):
        """Add a candidate signal to the pool."""
        now = datetime.now(UTC)
        async with self._lock:
            symbol_pool = self._pool[signal.symbol_id]
            symbol_pool[signal.strategy_name] = signal
            
            if signal.symbol_id not in self._first_signal_time:
                self._first_signal_time[signal.symbol_id] = now

            # Persist to Redis
            try:
                redis_key = f"{self._redis_key_prefix}{signal.symbol_id}"
                await redis_client.hset(redis_key, signal.strategy_name, _serialize_signal(signal))
                await redis_client.expire(redis_key, max(15, self.window_seconds * 3))
            except Exception as e:
                logger.error(f"Failed to persist signal to Redis: {e}")
                
        logger.debug(f"Added {signal.strategy_name} signal for symbol {signal.symbol_id} to pool.")

    async def _flush_loop(self):
        """Background loop to flush signal groups that exceed the time window."""
        while True:
            try:
                await asyncio.sleep(0.5)  # Check aggressively (reduced from 2.0 to 0.5)
                await self._check_and_flush()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception(f"Error in CandidateSignalPool flush loop: {e}")

    async def _check_and_flush(self):
        """Check all symbol pools and flush those that have reached window_seconds maturity."""
        now = datetime.now(UTC)
        to_flush: List[List[CandidateSignal]] = []

        async with self._lock:
            for symbol_id, first_time in list(self._first_signal_time.items()):
                age = (now - first_time).total_seconds()
                
                # If the signal group has lived in the pool for >= window_seconds, flush it
                if age >= self.window_seconds:
                    signals = list(self._pool[symbol_id].values())
                    if signals:
                        to_flush.append(signals)
                    
                    # Clear out the flushed symbols from pool memory
                    self._pool.pop(symbol_id, None)
                    self._first_signal_time.pop(symbol_id, None)

                    # Remove from Redis
                    try:
                        redis_key = f"{self._redis_key_prefix}{symbol_id}"
                        # We append task so it doesn't block the flush loop
                        asyncio.create_task(redis_client.delete(redis_key))
                    except Exception as e:
                        logger.error(f"Error deleting signal pool key from Redis: {e}")

        # Dispatch flushed signal groups outside the lock
        cb = self._consolidation_callback
        if to_flush and cb is not None:
            for signal_group in to_flush:
                try:
                    await cb(signal_group)
                except Exception as e:
                    logger.exception(f"Error executing consolidation callback for flushed signals: {e}")

# Global Instance
signal_pool = CandidateSignalPool()

