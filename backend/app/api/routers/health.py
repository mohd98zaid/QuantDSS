"""Health check router."""
from datetime import datetime, time, timezone, timedelta

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text as sa_text

from app.api.deps import get_current_user
from app.core.database import engine
from app.core.redis import check_redis_health

router = APIRouter()

# IST offset
_IST = timezone(timedelta(hours=5, minutes=30))
# NSE market hours (IST)
_MARKET_OPEN = time(9, 15)
_MARKET_CLOSE = time(15, 30)


def _is_market_open() -> bool:
    """Return True if NSE market is currently open (Mon–Fri, 09:15–15:30 IST)."""
    now_ist = datetime.now(_IST)
    if now_ist.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    current_time = now_ist.time()
    return _MARKET_OPEN <= current_time < _MARKET_CLOSE


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

    from app.ingestion.broker_manager import broker_manager
    active = broker_manager.get_active_broker()
    broker_ok = False
    if active:
        try:
            b_status = await active.get_connection_status()
            broker_ok = b_status.get("status") == "CONNECTED"
        except Exception:
            pass

    status = "ok" if (db_ok and redis_ok) else "degraded"
    return {
        "status": status,
        "database": "ok" if db_ok else "error",
        "redis": "ok" if redis_ok else "error",
        "broker": "ok" if broker_ok else "disconnected",
    }


@router.get("/health/db")
async def db_health():
    """Database specific health check."""
    try:
        async with engine.connect() as conn:
            await conn.execute(sa_text("SELECT 1"))
        return {"status": "ok", "message": "Database is reachable"}
    except Exception as e:
        return {"status": "error", "message": f"Database connection failed: {str(e)}"}


@router.get("/health/redis")
async def redis_health():
    """Redis specific health check."""
    try:
        redis_ok = await check_redis_health()
        if redis_ok:
            return {"status": "ok", "message": "Redis is reachable"}
        else:
            return {"status": "error", "message": "Redis connection failed"}
    except Exception as e:
        return {"status": "error", "message": f"Redis connection error: {str(e)}"}


@router.get("/health/broker")
async def broker_health():
    """Broker connection status."""
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


class TokenUpdateRequest(BaseModel):
    token: str


@router.patch("/health/broker/token")
async def update_broker_token(
    body: TokenUpdateRequest,
    _user: dict = Depends(get_current_user),
):
    """
    Update the Upstox access token in-memory — no Docker restart required.

    The recovery monitor will pick it up on the next 60-second ping and
    hot-swap back to Upstox automatically if the token is valid.
    Also writes the token back to .env so it survives container restarts.
    """
    import re
    from pathlib import Path
    from fastapi import HTTPException
    from app.ingestion.adapters.upstox_adapter import set_token_override

    token = body.token.strip()
    if not token:
        raise HTTPException(status_code=400, detail="Token cannot be empty")

    # 1. Update in-memory (recovery task picks this up within 60 s)
    set_token_override(token)

    # 2. Write back to .env so it survives restarts
    env_paths = [Path("/app").parent / ".env"] # Docker fallback
    try:
        env_paths.append(Path(__file__).resolve().parents[4] / ".env")
    except Exception:
        pass
        
    written = False
    for env_path in env_paths:
        if env_path.exists():
            try:
                content = env_path.read_text()
                if re.search(r"^UPSTOX_ACCESS_TOKEN=", content, re.MULTILINE):
                    content = re.sub(
                        r"^UPSTOX_ACCESS_TOKEN=.*$",
                        f"UPSTOX_ACCESS_TOKEN={token}",
                        content,
                        flags=re.MULTILINE,
                    )
                else:
                    content += f"\nUPSTOX_ACCESS_TOKEN={token}\n"
                env_path.write_text(content)
                written = True
            except Exception:
                pass
            break

    return {
        "status": "ok",
        "message": "Token updated in-memory. Recovery monitor will reconnect Upstox within 60 s.",
        "env_file_updated": written,
    }


@router.get("/health/market")
async def market_status():
    """
    Return whether the NSE market is currently open.

    Priority:
      1. Upstox Exchange Status API — aware of holidays and real NSE status
      2. IST time-based calculation  — fallback when Upstox token unavailable
    """
    now_ist = datetime.now(_IST)
    current_time_ist = now_ist.strftime("%H:%M")

    # Try Upstox Exchange Status API first (holiday-aware)
    try:
        from app.ingestion.upstox_http import UpstoxHTTPClient, UpstoxTokenError
        client = UpstoxHTTPClient()
        upstox_data = await client.get_market_status("NSE")
        is_open = upstox_data.get("is_open", False)
        return {
            "is_open": is_open,
            "status": "OPEN" if is_open else "CLOSED",
            "current_time_ist": current_time_ist,
            "market_open": "09:15",
            "market_close": "15:30",
            "source": "upstox",
            "exchange_status": upstox_data.get("status", ""),
        }
    except Exception:
        pass

    open_flag = _is_market_open()
    return {
        "is_open": open_flag,
        "status": "OPEN" if open_flag else "CLOSED",
        "current_time_ist": current_time_ist,
        "market_open": "09:15",
        "market_close": "15:30",
        "source": "local_time",
    }
