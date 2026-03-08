"""
Signal Deduplication — Fix M-04.

Prevents the same strategy from firing multiple signals for the same
symbol on the same candle timestamp. Uses a TTL-based cache that
auto-expires entries after 60 seconds.

Fix (Audit): Replaced datetime.utcnow() with datetime.now(UTC) for
timezone-aware comparisons.
"""
from datetime import timezone, datetime, timedelta
from typing import Dict, Tuple

from app.core.redis_client import get_redis


class SignalDeduplicator:
    """
    Tracks (symbol_id, strategy_id, candle_time) tuples to prevent duplicate signals.

    Usage:
        dedup = SignalDeduplicator()
        if dedup.is_duplicate(signal):
            return None  # skip duplicate
        dedup.record(signal)
    """

    def __init__(self, ttl_seconds: int = 60):
        self._ttl_seconds = ttl_seconds

    def _make_key(self, symbol_id: int, strategy_id: int, candle_time) -> str:
        """Create a unique key from signal attributes."""
        ct_str = candle_time.isoformat() if hasattr(candle_time, "isoformat") else str(candle_time)
        return f"dedup:sig:{symbol_id}:{strategy_id}:{ct_str}"

    async def is_duplicate(self, symbol_id: int, strategy_id: int, candle_time) -> bool:
        """
        Check if this signal was already recorded within the TTL window.
        Returns False if the signal is NEW (successfully acquired lock).
        Returns True if the signal is a DUPLICATE (lock already exists).
        
        Note: This implicitly "records" the signal if it wasn't a duplicate. 
        """
        key = self._make_key(symbol_id, strategy_id, candle_time)
        redis = await get_redis()
        
        # SET NX (Not eXists) EX (EXpire in seconds)
        # Returns True if the key was set, False if it already existed
        is_new = await redis.set(key, "1", nx=True, ex=self._ttl_seconds)
        
        return not is_new


# Module-level singleton (now stateless)
signal_dedup = SignalDeduplicator()
