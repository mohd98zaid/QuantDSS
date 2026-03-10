from fastapi import APIRouter, Depends, HTTPException, Body, Request
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Dict, Any

from app.core.database import get_db
from app.core.redis import get_redis
from app.core.rate_limit import limiter
from app.system.trading_state import set_trading_state, get_trading_state

router = APIRouter(prefix="/admin/trading", tags=["Admin: Trading Control"])

# In a real app, you would add a dependency here to verify the user is an admin.
# For example: dependencies=[Depends(verify_admin_user)]

@router.get("/state")
async def get_current_trading_state(
    redis=Depends(get_redis)
) -> Dict[str, str]:
    """Get the current global trading state."""
    state = await get_trading_state(redis)
    return {"status": "success", "state": state}

@router.post("/disable")
@limiter.limit("10/minute")
async def disable_trading(
    request: Request,
    payload: Dict[str, Any] = Body(...),
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis)
) -> Dict[str, str]:
    """Halt all new trading globally using the kill switch."""
    trigger = payload.get("trigger", "manual:admin_dashboard")
    reason = payload.get("reason", "Admin requested manual kill switch")
    
    await set_trading_state("DISABLED", trigger, reason, db_session=db, redis_client=redis)
    return {"status": "success", "message": "Trading disabled globally"}

@router.post("/enable")
@limiter.limit("10/minute")
async def enable_trading(
    request: Request,
    payload: Dict[str, Any] = Body(...),
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis)
) -> Dict[str, str]:
    """Resume all trading operations globally."""
    trigger = payload.get("trigger", "manual:admin_dashboard")
    reason = payload.get("reason", "Admin requested trading resumption")
    
    await set_trading_state("ENABLED", trigger, reason, db_session=db, redis_client=redis)
    return {"status": "success", "message": "Trading enabled globally"}

@router.post("/emergency-flatten")
@limiter.limit("5/minute")
async def emergency_flatten(
    request: Request,
    payload: Dict[str, Any] = Body(...),
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis)
) -> Dict[str, str]:
    """Halt all trading AND aggressively close all open positions."""
    trigger = payload.get("trigger", "manual:admin_dashboard")
    reason = payload.get("reason", "Admin requested EMERGENCY FLATTEN")
    
    await set_trading_state("EMERGENCY_FLATTEN", trigger, reason, db_session=db, redis_client=redis)
    return {"status": "success", "message": "Emergency flatten initiated"}
