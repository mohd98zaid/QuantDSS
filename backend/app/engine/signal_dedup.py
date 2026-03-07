"""
Signal Deduplication — Fix M-04.

Prevents the same strategy from firing multiple signals for the same
symbol on the same candle timestamp. Uses a TTL-based cache that
auto-expires entries after 60 seconds.

Fix (Audit): Replaced datetime.utcnow() with datetime.now(UTC) for
timezone-aware comparisons.
"""
from datetime import UTC, datetime, timedelta
from typing import Dict, Tuple


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
        self._seen: Dict[Tuple[int, int, str], datetime] = {}
        self._ttl = timedelta(seconds=ttl_seconds)

    def _make_key(self, symbol_id: int, strategy_id: int, candle_time) -> Tuple[int, int, str]:
        """Create a unique key from signal attributes."""
        ct_str = candle_time.isoformat() if hasattr(candle_time, "isoformat") else str(candle_time)
        return (symbol_id, strategy_id, ct_str)

    def is_duplicate(self, symbol_id: int, strategy_id: int, candle_time) -> bool:
        """Check if this signal was already recorded within the TTL window."""
        key = self._make_key(symbol_id, strategy_id, candle_time)
        entry = self._seen.get(key)
        if entry and (datetime.now(UTC) - entry) < self._ttl:
            return True
        return False

    def record(self, symbol_id: int, strategy_id: int, candle_time):
        """Record that we've seen this signal."""
        key = self._make_key(symbol_id, strategy_id, candle_time)
        self._seen[key] = datetime.now(UTC)

    def cleanup(self):
        """Remove expired entries to prevent memory leaks."""
        now = datetime.now(UTC)
        expired = [k for k, v in self._seen.items() if (now - v) >= self._ttl]
        for k in expired:
            del self._seen[k]


# Module-level singleton
signal_dedup = SignalDeduplicator()
