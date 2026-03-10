"""
Watchdog Worker — monitors Redis Streams for lag and reclaims stuck messages.
"""
import asyncio
from app.workers.base import WorkerBase
from app.core.redis import redis_client
from app.core.logging import logger
from app.core.metrics import REDIS_STREAM_LAG

STREAMS_TO_MONITOR = {
    "stream:signals:raw": "risk_engine",
    "stream:signals:validated": "execution_engine",
    "stream:trades:to_execute": "trade_monitor",
}

class WatchdogWorker(WorkerBase):
    NAME = "watchdog"

    async def run(self):
        logger.info("[Watchdog] Started monitoring Redis Streams")
        while self.is_running:
            try:
                await self._check_streams()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"[Watchdog] Error in monitoring loop: {e}")
            await asyncio.sleep(60)

    async def _check_streams(self):
        for stream, group in STREAMS_TO_MONITOR.items():
            try:
                # Get lag (pending count from xpending)
                pending_info = await redis_client.xpending(stream, group)
                if pending_info and pending_info.get("pending"):
                    lag = pending_info.get("pending", 0)
                    REDIS_STREAM_LAG.labels(stream_name=stream, consumer_group=group).set(lag)
                    
                    if lag > 100:
                        logger.warning(f"[Watchdog] HIGH LAG on {stream}: {lag} messages pending")
                    
                    # Check for stuck messages (> 2 mins)
                    stuck = await redis_client.xpending_range(
                        stream, group, min="-", max="+", count=100
                    )
                    
                    if not stuck:
                        continue
                        
                    reclaimed_count = 0
                    for msg in stuck:
                        msg_id, consumer, idle_time, deliveries = msg.get("message_id"), msg.get("consumer"), msg.get("time_since_delivered"), msg.get("deliveries")
                        if not msg_id or not idle_time: 
                            continue
                            
                        if idle_time > 120_000: # 120 seconds = 2 minutes
                            logger.error(f"[Watchdog] Message {msg_id} stuck on {stream} by {consumer} for {idle_time}ms")
                            
                            # Move to DLQ
                            messages = await redis_client.xrange(stream, msg_id, msg_id)
                            if messages:
                                _, fields = messages[0]
                                await redis_client.xadd("stream:dlq", fields)
                            
                            # Ack original
                            await redis_client.xack(stream, group, msg_id)
                            reclaimed_count += 1
                            
                    if reclaimed_count > 0:
                        logger.info(f"[Watchdog] Moved {reclaimed_count} stuck messages from {stream} to DLQ")
                else:
                    REDIS_STREAM_LAG.labels(stream_name=stream, consumer_group=group).set(0)

            except Exception as e:
                logger.debug(f"[Watchdog] Stream {stream} or group {group} might not exist yet: {e}")

if __name__ == "__main__":
    WatchdogWorker().main()
