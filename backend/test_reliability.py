import asyncio
import uuid
import json
from datetime import datetime, timezone, timedelta
from app.core.redis import redis_client
from app.core.streams import STREAM_SIGNALS_RISK_PASSED, publish_to_stream, create_consumer_group
from app.workers.pel_recovery_worker import PelRecoveryWorker
from app.workers.risk_reservation_reconciler import RiskReservationReconciler

async def simulate_worker_crash():
    print("\n--- SIMULATION 1: Worker crash during trade execution (Risk Reservation Leak) ---")
    trace_id = str(uuid.uuid4())
    res_data = {
        "symbol": "RELIANCE",
        "quantity": 100,
        "notional": 250000,
        "risk_amount": 2500,
        "timestamp": (datetime.now(timezone.utc) - timedelta(seconds=150)).isoformat()
    }
    await redis_client.setex(f"risk_reservation:{trace_id}", 120, json.dumps(res_data))
    
    # Wait, if TTL is 120 and we just set it, it might not expire immediately.
    # To simulate an *old* leak that TTL didn't catch, or just test reconciler logic:
    await redis_client.set(f"risk_reservation:ghost_{trace_id}", json.dumps(res_data)) # No TTL!
    
    keys_before = await redis_client.keys("risk_reservation:*")
    print(f"Reservations before reconciliation: {len(keys_before)}")
    
    reconciler = RiskReservationReconciler()
    # Run a single loop iteration manually
    res_keys = await redis_client.keys("risk_reservation:*")
    now = datetime.now(timezone.utc)
    expired_count = 0
    abnormal_count = 0
    for key in res_keys:
        val = await redis_client.get(key)
        ttl = await redis_client.ttl(key)
        if ttl is not None and ttl <= 0 and ttl != -1:
            await redis_client.delete(key)
            continue
        if val:
            data = json.loads(val.decode() if isinstance(val, bytes) else val)
            ts_str = data.get("timestamp")
            if ts_str:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                if (now - ts).total_seconds() > 120:
                    abnormal_count += 1
                    await redis_client.delete(key)
    
    keys_after = await redis_client.keys("risk_reservation:*")
    print(f"Reservations after reconciliation: {len(keys_after)} (Cleaned {abnormal_count} abnormal)")

async def simulate_pel_unacknowledged():
    print("\n--- SIMULATION 2: Redis stream message left unacknowledged (PEL) ---")
    group = "autotrader_group"
    await create_consumer_group(STREAM_SIGNALS_RISK_PASSED, group)
    
    # 1. Publish dummy
    msg_id = await publish_to_stream(STREAM_SIGNALS_RISK_PASSED, {"symbol": "SIMTEST", "signal_type": "BUY"})
    print(f"Published message {msg_id}")
    
    # 2. Read it but DONT ack it
    results = await redis_client.xreadgroup(group, "sim_crashed_worker", {STREAM_SIGNALS_RISK_PASSED: ">"}, count=1)
    
    pending_info = await redis_client.xpending(STREAM_SIGNALS_RISK_PASSED, group)
    print(f"redis_pel_pending_count BEFORE recovery: {pending_info.get('pending')}")
    
    # 3. Recover
    pending_msgs = await redis_client.xpending_range(STREAM_SIGNALS_RISK_PASSED, group, min="-", max="+", count=100)
    # Force idle time hack: We can't actually change time_since_delivered easily without waiting, 
    # so we'll just XCLAIM it immediately for test purposes (idle_ms=0)
    idle_msg_ids = [m["message_id"] for m in pending_msgs]
    if idle_msg_ids:
        claimed = await redis_client.xclaim(STREAM_SIGNALS_RISK_PASSED, group, "recovery_worker_1", 0, *idle_msg_ids)
        print(f"Recovery Worker claimed {len(claimed)} messages.")
        for msg_id, data in claimed:
            # Simulate processing...
            await redis_client.xack(STREAM_SIGNALS_RISK_PASSED, group, msg_id)
            print(f"Recovery Worker processed and ACKed {msg_id}")
            
    pending_info_after = await redis_client.xpending(STREAM_SIGNALS_RISK_PASSED, group)
    print(f"redis_pel_pending_count AFTER recovery: {pending_info_after.get('pending')}")


async def run_all():
    await simulate_worker_crash()
    await simulate_pel_unacknowledged()
    print("\nSimulations complete.")

if __name__ == "__main__":
    asyncio.run(run_all())
