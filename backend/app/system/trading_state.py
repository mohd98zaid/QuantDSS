import json
import logging
from datetime import datetime, timezone
from app.core.redis import redis_client

logger = logging.getLogger(__name__)

REDIS_GLOBAL_TRADING_STATE_KEY = "quantdss:global_trading_state"

async def get_trading_state(redis=None) -> str:
    """Gets the global trading state."""
    if redis is None:
        redis = redis_client
    
    state = await redis.get(REDIS_GLOBAL_TRADING_STATE_KEY)
    if state:
        return state.decode("utf-8") if isinstance(state, bytes) else state
    return "ENABLED"

async def set_trading_state(state: str, triggered_by: str, reason: str = "", redis=None, db_session=None) -> None:
    """Sets the global trading state and logs the event."""
    if state not in ["ENABLED", "DISABLED", "EMERGENCY_FLATTEN"]:
        raise ValueError(f"Invalid trading state: {state}")
        
    if redis is None:
        redis = redis_client
        
    await redis.set(REDIS_GLOBAL_TRADING_STATE_KEY, state)
    logger.warning(f"Global trading state changed to {state} by {triggered_by}. Reason: {reason}")
    
    if db_session:
        try:
            from app.models.kill_switch_event import KillSwitchEvent
            
            event = KillSwitchEvent(
                triggered_by=triggered_by,
                reason=reason,
                state=state,
                triggered_at=datetime.now(timezone.utc)
            )
            db_session.add(event)
            await db_session.commit()
        except Exception as e:
            logger.error(f"Failed to log kill switch event to database: {e}")

async def is_trading_enabled(redis=None) -> bool:
    """Checks if trading is globally enabled (not disabled or emergency flattening)."""
    state = await get_trading_state(redis)
    return state == "ENABLED"
