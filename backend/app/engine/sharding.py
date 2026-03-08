"""
ShardManager — Deterministic symbol → worker mapping for horizontal scaling.

Uses hash-based partitioning to assign each symbol to exactly one worker.
This ensures:
  - No duplicate processing across workers
  - Consistent assignment (same symbol always maps to same worker)
  - Roughly even distribution across shards

Environment Variables:
  SIGNAL_WORKER_ID     — This worker's shard index (0-based)
  SIGNAL_WORKER_TOTAL  — Total number of signal engine workers

Usage:
    shard = ShardManager(worker_id=0, total_workers=3)
    if shard.owns("RELIANCE"):
        process_candle(...)
"""
from __future__ import annotations

import hashlib
from app.core.logging import logger


class ShardManager:
    """
    Deterministic hash-based symbol-to-worker partitioner.

    All workers use the same hash function, so a given symbol will always
    map to the same shard index regardless of which worker evaluates it.
    """

    def __init__(self, worker_id: int = 0, total_workers: int = 1):
        if total_workers < 1:
            raise ValueError(f"total_workers must be >= 1, got {total_workers}")
        if worker_id < 0 or worker_id >= total_workers:
            raise ValueError(
                f"worker_id must be in [0, {total_workers}), got {worker_id}"
            )
        self.worker_id = worker_id
        self.total_workers = total_workers
        logger.info(
            f"ShardManager initialized: worker_id={worker_id}, "
            f"total_workers={total_workers}"
        )

    @staticmethod
    def _hash_symbol(symbol: str) -> int:
        """
        Stable hash for a symbol string.

        Uses MD5 for deterministic, cross-platform consistency.
        Python's built-in hash() is randomized per process (PYTHONHASHSEED).
        """
        return int(hashlib.md5(symbol.encode("utf-8")).hexdigest(), 16)

    def get_shard(self, symbol: str) -> int:
        """Return the shard index (0-based) for a given symbol."""
        return self._hash_symbol(symbol) % self.total_workers

    def owns(self, symbol: str) -> bool:
        """Return True if this worker is responsible for the given symbol."""
        if self.total_workers <= 1:
            return True  # Single worker owns everything
        return self.get_shard(symbol) == self.worker_id
