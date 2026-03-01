"""Signals router — Signal history and filtering (stub for Phase 1)."""
from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_session
from app.models.signal import Signal
from app.schemas.signal import SignalListResponse, SignalResponse

router = APIRouter()


@router.get("/signals", response_model=SignalListResponse)
async def list_signals(
    status: str | None = Query(None, description="APPROVED / BLOCKED / SKIPPED / ALL"),
    symbol: str | None = None,
    strategy_id: int | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_session),
    _user: dict = Depends(get_current_user),
):
    """Fetch paginated signals with filters."""
    query = select(Signal).order_by(Signal.timestamp.desc())

    if status and status.upper() != "ALL":
        query = query.where(Signal.risk_status == status.upper())
    if strategy_id:
        query = query.where(Signal.strategy_id == strategy_id)
    if from_date:
        query = query.where(func.date(Signal.timestamp) >= from_date)
    if to_date:
        query = query.where(func.date(Signal.timestamp) <= to_date)

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    # Paginate
    query = query.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    signals = result.scalars().all()

    return SignalListResponse(
        total=total,
        page=page,
        page_size=page_size,
        signals=[SignalResponse.model_validate(s) for s in signals],
    )


@router.get("/signals/{signal_id}", response_model=SignalResponse)
async def get_signal(
    signal_id: int,
    db: AsyncSession = Depends(get_session),
    _user: dict = Depends(get_current_user),
):
    """Get full detail of a single signal."""
    from fastapi import HTTPException
    result = await db.execute(select(Signal).where(Signal.id == signal_id))
    signal = result.scalar_one_or_none()
    if not signal:
        raise HTTPException(status_code=404, detail="Signal not found")
    return SignalResponse.model_validate(signal)
