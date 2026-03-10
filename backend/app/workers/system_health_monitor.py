"""
System Health Monitor Worker

Checks all registered workers' heartbeats and alerts if a worker goes dark.
"""
import asyncio
from app.core.redis import redis_client
from app.core.logging import logger

class SystemHealthMonitor:
    """
    Monitors all known workers by checking their Redis heartbeat keys.
    If a key expires (TTL 10s), the worker is presumed dead.
    """
    NAME = "system-health-monitor"

    def __init__(self):
        self._expected_workers = [
            "signal-engine", 
            "risk-engine-worker", 
            "position-reconciler", 
            "autotrader", 
            "pel-recovery-worker"
        ]

    async def run(self):
        logger.info(f"[{self.NAME}] Started monitoring worker heartbeats...")
        while True:
            for worker_name in self._expected_workers:
                try:
                    is_alive = await redis_client.get(f"worker_heartbeat:{worker_name}")
                    if not is_alive:
                        # Log CRITICAL alert to trigger Prom/Telegram integration
                        logger.critical(
                            f"[{self.NAME}] WORKER FAILURE DETECTED: {worker_name} has missed its heartbeat! "
                            f"Initiating auto-restart sequence or escalating alert."
                        )
                        # In k8s/Docker Swarm, we would rely on exit checks. For now, alert heavily.
                except Exception as e:
                    logger.error(f"[{self.NAME}] Error checking heartbeat for {worker_name}: {e}")
            
            # Health monitor also updates its own heartbeat
            try:
                await redis_client.setex(f"worker_heartbeat:{self.NAME}", 10, "alive")
            except Exception:
                pass

            await asyncio.sleep(5)

if __name__ == "__main__":
    import uvloop
    uvloop.install()
    asyncio.run(SystemHealthMonitor().run())
