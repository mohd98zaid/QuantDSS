"""
Risk Reservation Layer

Provides atomicity to prevent concurrent signals from exceeding overall risk limits.
Reservations are stored in Redis with a TTL of 120 seconds.
"""
import json
from datetime import datetime, timezone

from app.core.redis import redis_client
from app.core.logging import logger


async def reserve_risk(signal_id: str, symbol: str, risk_amount: float, quantity: int, notional: float) -> bool:
    """
    Reserves a specified amount of risk for a symbol.
    """
    key = f"risk_reservation:{signal_id}"
    data = {
        "symbol": symbol,
        "quantity": quantity,
        "notional": notional,
        "risk_amount": risk_amount,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    
    try:
        await redis_client.setex(key, 120, json.dumps(data))
        logger.debug(f"[RiskReservation] Reserved {risk_amount} risk for {symbol} (trace_id: {signal_id})")
        return True
    except Exception as e:
        logger.error(f"[RiskReservation] Failed to reserve risk for {symbol}: {e}")
        return False


async def get_total_reserved_risk() -> tuple[float, list[float]]:
    """
    Scans all active risk reservations and returns the aggregated 
    total risk amount and a list of positional notionals.
    Returns: (total_reserved_risk, list_of_notionals)
    """
    total_risk = 0.0
    notionals = []
    
    try:
        cursor = b"0"
        keys = []
        while cursor:
            cursor, k = await redis_client.scan(cursor=cursor, match="risk_reservation:*", count=100)
            keys.extend(k)

        if not keys:
            return 0.0, []
        
        for key in keys:
            val = await redis_client.get(key)
            if val:
                data = json.loads(val.decode() if isinstance(val, bytes) else val)
                total_risk += float(data.get("risk_amount", 0.0))
                notionals.append(float(data.get("notional", 0.0)))
    except Exception as e:
        logger.error(f"[RiskReservation] Failed to fetch total reserved risk: {e}")
        
    return total_risk, notionals


async def release_reservation(signal_id: str) -> None:
    """
    Releases a specific risk reservation. Useful for early aborts or 
    immediate reconciliations post-execution.
    """
    try:
        await redis_client.delete(f"risk_reservation:{signal_id}")
        logger.debug(f"[RiskReservation] Released reservation {signal_id}")
    except Exception as e:
        logger.error(f"[RiskReservation] Failed to release reservation {signal_id}: {e}")
