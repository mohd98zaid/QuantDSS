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
        
        # Fix Group 5: Initialize RiskEngine context
        try:
            await self._risk_engine._load_risk_config()
            logger.info(f"[{self.NAME}] Successfully loaded RiskEngine configuration")
        except Exception as e:
            logger.error(f"[{self.NAME}] Failed to load RiskEngine configuration: {e}")

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
                            msg_id = msg["message_id"]
                            deliveries = msg.get("times_delivered", msg.get("delivered", 1))
                            
                            # Fix Group 5: Poison pill handling
                            if deliveries > 5:
                                logger.error(f"[{self.NAME}] Poison pill detected (deliveries={deliveries}) for msg {msg_id} on {stream}. Acknowledging to skip.")
                                await redis_client.xack(stream, group, msg_id)
                                continue

                            if msg.get("time_since_delivered", 0) >= self.idle_ms:
                                idle_msg_ids.append(msg_id)

                        if idle_msg_ids:
                            logger.info(f"[{self.NAME}] Claiming {len(idle_msg_ids)} idle messages from {stream}")
                            
                            claimed = await redis_client.xclaim(
                                stream, group, self.consumer_name, self.idle_ms, idle_msg_ids
                            )
                            
                            for msg_id, data in claimed:
                                decoded = {}
                                for k, v in data.items():
                                    key = k.decode() if isinstance(k, bytes) else k
                                    val = v.decode() if isinstance(v, bytes) else v
                                    decoded[key] = val
                                
                                # Fix Group 5: Stale Signal Rejection
                                msg_id_str = msg_id.decode() if isinstance(msg_id, bytes) else str(msg_id)
                                try:
                                    msg_ts_ms = int(msg_id_str.split('-')[0])
                                    msg_time = datetime.fromtimestamp(msg_ts_ms / 1000.0, tz=timezone.utc)
                                    now = datetime.now(timezone.utc)
                                    if (now - msg_time).total_seconds() > 300:
                                        logger.warning(f"[{self.NAME}] Discarding stale PEL message {msg_id_str} (> 5 mins old)")
                                        await redis_client.xack(stream, group, msg_id)
                                        continue
                                except Exception as e:
                                    logger.error(f"[{self.NAME}] Failed to parse msg_id {msg_id_str} for stale check: {e}")
                                
                                logger.info(f"[{self.NAME}] Reprocessing claimed message {msg_id} via {handler.__name__}")
                                await handler(msg_id, decoded)
                                await redis_client.xack(stream, group, msg_id)

                except Exception as e:
                    logger.exception(f"[{self.NAME}] Error in recovery loop for {stream}: {e}")

            await asyncio.sleep(10)

if __name__ == "__main__":
    PelRecoveryWorker().main()
