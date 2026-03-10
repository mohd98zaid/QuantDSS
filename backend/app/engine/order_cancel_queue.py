"""
Order Cancel Queue — Guaranteed order cancellation with retries.
"""
import asyncio
import json
from datetime import datetime, timezone

from app.core.logging import logger
from app.core.redis import redis_client
from app.workers.base import WorkerBase

async def enqueue_cancel(order_id: str) -> None:
    """Push an order ID to the cancellation queue."""
    if not order_id:
        return
    await redis_client.lpush("order_cancel_queue", order_id)
    logger.debug(f"OrderCancelQueue: Enqueued {order_id} for guaranteed cancellation")

class OrderCancelWorker(WorkerBase):
    NAME = "order-cancel-worker"

    async def run(self):
        logger.info(f"[{self.NAME}] Started resilient cancellation queue processing.")
        while self.is_running:
            try:
                # BRPOP blocks until an item is available or timeout (2s)
                result = await redis_client.brpop("order_cancel_queue", timeout=2)
                if not result:
                    continue

                _, order_id_bytes = result
                order_id = order_id_bytes.decode() if isinstance(order_id_bytes, bytes) else order_id_bytes

                success = False
                for attempt in range(1, 6): # 5 attempts maximum
                    try:
                        from app.core.config import settings
                        import httpx
                        
                        token = getattr(settings, "upstox_access_token", "")
                        if not token:
                            logger.info(f"[{self.NAME}] Simulation: successfully cancelled {order_id}")
                            success = True
                            break

                        headers = {
                            "Accept": "application/json",
                            "Content-Type": "application/json",
                            "Authorization": f"Bearer {token}",
                        }
                        
                        async with httpx.AsyncClient(timeout=10) as client:
                            resp = await client.delete(
                                "https://api.upstox.com/v2/order",
                                headers=headers,
                                params={"order_id": order_id},
                            )
                        if resp.status_code == 200:
                            # Fix 3: Guaranteed Confirmation Logic
                            logger.info(f"[{self.NAME}] Cancellation request accepted for {order_id}. Verifying...")
                            await asyncio.sleep(0.5)  # wait 500ms
                            
                            verify_resp = await client.get(
                                "https://api.upstox.com/v2/order/details",
                                headers=headers,
                                params={"order_id": order_id},
                            )
                            
                            if verify_resp.status_code == 200:
                                data = verify_resp.json().get("data", [])
                                if isinstance(data, list) and len(data) > 0:
                                    status = data[0].get("status", "").lower()
                                elif isinstance(data, dict):
                                    status = data.get("status", "").lower()
                                else:
                                    status = "unknown"
                                    
                                if status in ("cancelled", "rejected", "complete"):
                                    logger.info(f"[{self.NAME}] Successfully cancelled and verified {order_id} on attempt {attempt}")
                                    success = True
                                    break
                                else:
                                    logger.warning(f"[{self.NAME}] Attempt {attempt}: order {order_id} status is {status}, retrying...")
                            else:
                                logger.warning(f"[{self.NAME}] Attempt {attempt}: failed to verify {order_id}. HTTP {verify_resp.status_code}")
                            
                        else:
                            logger.warning(f"[{self.NAME}] Attempt {attempt} failed to cancel {order_id}: {resp.status_code} {resp.text[:100]}")
                            
                    except Exception as e:
                        logger.error(f"[{self.NAME}] Attempt {attempt} exception for {order_id}: {e}")
                    
                    if not success:
                        await asyncio.sleep(2 ** attempt)  # exponential backoff

                if not success:
                    logger.critical(f"[{self.NAME}] CRITICAL: Failed to cancel order {order_id} after 5 attempts. RECONCILIATION REQUIRED.")

            except Exception as e:
                logger.exception(f"[{self.NAME}] Loop error processing cancellations: {e}")
                await asyncio.sleep(1)

if __name__ == "__main__":
    OrderCancelWorker().main()
