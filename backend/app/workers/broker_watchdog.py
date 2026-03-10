"""
Broker Watchdog Worker

Checks Broker API connectivity persistently.
"""
import asyncio
from datetime import datetime, timezone
import time

from app.core.logging import logger
from app.ingestion.broker_manager import get_broker_client

class BrokerWatchdogWorker:
    """
    Monitors the API connection to the live broker.
    Exposes broker_api_errors metric if connection fails.
    """
    NAME = "broker-watchdog"

    async def run(self):
        logger.info(f"[{self.NAME}] Started watchdog...")
        while True:
            try:
                client = get_broker_client()
                
                start = time.perf_counter()
                
                # Ping the user profile or funds endpoint as a healthcheck
                if hasattr(client, 'get_profile'):
                    await client.get_profile()
                elif hasattr(client, 'get_funds'):
                    await client.get_funds()
                    
                latency = (time.perf_counter() - start) * 1000
                logger.debug(f"[{self.NAME}] Broker API healthy. Latency: {latency:.2f}ms")
                
            except Exception as e:
                # E.g. 502 Bad Gateway, 401 Unauthorized
                logger.error(f"[{self.NAME}] BROKER_API_ERROR: {str(e)}")
                # Emit metric for Observability (Layer 6)
                from app.core.redis import redis_client
                try:
                    await redis_client.incr("metrics:broker_api_errors")
                except Exception:
                    pass
            
            # Backoff for 15s
            await asyncio.sleep(15)

if __name__ == "__main__":
    import uvloop
    uvloop.install()
    asyncio.run(BrokerWatchdogWorker().run())
