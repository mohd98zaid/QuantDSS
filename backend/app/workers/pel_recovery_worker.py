"""
PEL Recovery Worker (Fix 2 & 3)

Scans Redis Streams for messages stuck in the Pending Entries List (PEL).
Claims and reprocesses messages that have been idle too long.
Exposes redis_pel_pending_count metric.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from app.core.logging import logger
from app.core.redis import redis_client
from app.core.streams import STREAM_SIGNALS_RISK_PASSED, STREAM_SIGNALS_APPROVED
from app.workers.base import WorkerBase
from app.workers.autotrader_worker import AutoTraderWorker
from app.workers.risk_engine_worker import RiskEngineWorker

class PelRecoveryWorker(WorkerBase):
    NAME = "pel-recovery-worker"
    
    def __init__(self, idle_ms: int = 10000):
        super().__init__()
        self.idle_ms = idle_ms
        self.consumer_name = "recovery_worker_1"
        self._autotrader = AutoTraderWorker()
        self._risk_engine = RiskEngineWorker()
        
        self.targets = [
            (STREAM_SIGNALS_APPROVED, "risk_engine_group", self._risk_engine._handle_signal),
            (STREAM_SIGNALS_RISK_PASSED, "autotrader_group", self._autotrader._handle_signal)
        ]

    async def run(self):
        logger.info(f"[{self.NAME}] Starting comprehensive PEL recovery")
        while self.is_running:
            for stream, group, handler in self.targets:
                try:
                    pending_info = await redis_client.xpending(stream, group)
                    if pending_info and pending_info.get("pending", 0) > 0:
                        pending_count = pending_info["pending"]
                        
                        logger.warning(
                            f"[{self.NAME}] ALERT: redis_pel_pending_count={pending_count} on {stream}:{group}"
                        )

                        pending_msgs = await redis_client.xpending_range(
                            stream, group, min="-", max="+", count=100
                        )
                        
                        idle_msg_ids = []
                        for msg in pending_msgs:
                            if msg["time_since_delivered"] >= self.idle_ms:
                                idle_msg_ids.append(msg["message_id"])

                        if idle_msg_ids:
                            logger.info(f"[{self.NAME}] Claiming {len(idle_msg_ids)} idle messages from {stream}")
                            
                            claimed = await redis_client.xclaim(
                                stream, group, self.consumer_name, self.idle_ms, idle_msg_ids
                            )
                            
                            for msg_id, data in claimed:
                                decoded = {}
                                for k, v in data.items():
                                    key = k.decode() if isinstance(k, bytes) else k
                                    val = v.decode() if isinstance(v, bytes) else v # Corrected: val should be v, not val
                                    decoded[key] = val
                                
                                logger.info(f"[{self.NAME}] Reprocessing claimed message {msg_id} via {handler.__name__}")
                                await handler(msg_id, decoded)
                                await redis_client.xack(stream, group, msg_id)

                except Exception as e:
                    logger.exception(f"[{self.NAME}] Error in recovery loop for {stream}: {e}")

            await asyncio.sleep(10)

if __name__ == "__main__":
    PelRecoveryWorker().main()
