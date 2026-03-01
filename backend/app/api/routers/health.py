"""Health check router."""
from fastapi import APIRouter
from sqlalchemy import text as sa_text

from app.core.database import engine
from app.core.redis import check_redis_health

router = APIRouter()


@router.get("/health")
async def health_check():
    """Overall system health check."""
    db_ok = True
    try:
        async with engine.connect() as conn:
            await conn.execute(sa_text("SELECT 1"))
    except Exception:
        db_ok = False

    redis_ok = await check_redis_health()

    status = "ok" if (db_ok and redis_ok) else "degraded"
    return {
        "status": status,
        "database": "ok" if db_ok else "error",
        "redis": "ok" if redis_ok else "error",
    }


@router.get("/health/broker")
async def broker_health():
    """Broker WebSocket connection status."""
    from app.ingestion.broker_manager import broker_manager
    
    active = broker_manager.get_active_broker()
    if active:
        status = await active.get_connection_status()
        return status
        
    return {
        "adapter": "none",
        "status": "NOT_CONFIGURED",
        "subscribed_symbols": [],
        "last_tick_at": None,
    }


