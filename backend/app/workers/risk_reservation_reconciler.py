"""
Risk Reservation Reconciler (Fix 4)

Periodically scans Redis for `risk_reservation:*`, removes expired, 
and logs abnormal reservations to self-heal the risk exposure locks.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

from app.core.logging import logger
from app.core.redis import redis_client
from app.workers.base import WorkerBase

class RiskReservationReconciler(WorkerBase):
    NAME = "risk-reservation-reconciler"

    async def run(self):
        logger.info(f"[{self.NAME}] Starting periodic reservation reconciliation...")
        while self.is_running:
            try:
                res_keys = await redis_client.keys("risk_reservation:*")
                now = datetime.now(timezone.utc)

                expired_count = 0
                abnormal_count = 0

                for key in res_keys:
                    key_str = key.decode() if isinstance(key, bytes) else key
                    
                    # Manual expiration backup (SETEX normally handles this, but helps with orphaned ghosts)
                    ttl = await redis_client.ttl(key)
                    if ttl is not None and ttl <= 0 and ttl != -1:
                        await redis_client.delete(key)
                        expired_count += 1
                        continue

                    val = await redis_client.get(key)
                    if val:
                        try:
                            data = json.loads(val.decode() if isinstance(val, bytes) else val)
                            ts_str = data.get("timestamp")
                            if ts_str:
                                # Standardize datetime parsing
                                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                                idle_seconds = (now - ts).total_seconds()
                                
                                if idle_seconds > 120:
                                    abnormal_count += 1
                                    logger.warning(
                                        f"[{self.NAME}] Abnormal reservation detected (old={idle_seconds}s): "
                                        f"{key_str} - {data}"
                                    )
                                    # Force remove if TTL didn't work for some reason
                                    await redis_client.delete(key)
                                    
                        except Exception as e:
                            logger.error(f"[{self.NAME}] Corrupt reservation {key_str}: {e}")
                            await redis_client.delete(key)

                if expired_count > 0 or abnormal_count > 0:
                    logger.info(
                        f"[{self.NAME}] Cleanup complete. "
                        f"Removed {expired_count} expired, cleaned {abnormal_count} abnormal."
                    )
                else:
                    logger.debug(f"[{self.NAME}] Checked {len(res_keys)} reservations. All healthy.")

            except Exception as e:
                logger.exception(f"[{self.NAME}] Error during reconciliation: {e}")

            await asyncio.sleep(30)

if __name__ == "__main__":
    RiskReservationReconciler().main()
