"""SSE Stream router — Real-time signal push via Server-Sent Events."""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.alerts.sse_manager import SSEManager
from app.api.deps import get_current_user_sse
from app.core.rate_limit import limiter

router = APIRouter()


@router.get("/stream/signals")
@limiter.limit("10/minute")
async def stream_signals(
    request: Request,
    _user: dict = Depends(get_current_user_sse)
):
    """
    Real-time signal stream for dashboard via SSE.
    Events: signal, risk_update, halt, heartbeat.
    """
    return StreamingResponse(
        SSEManager.event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
