"""Signals router — Signal history and filtering."""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_session
from app.models.signal import Signal
from app.models.symbol import Symbol
from app.models.strategy import Strategy
from app.schemas.signal import SignalListResponse, SignalResponse

router = APIRouter()


def _build_signal_response(sig: Signal, sym_name: str | None, strat_name: str | None) -> SignalResponse:
    """Build a SignalResponse, resolving the symbol/strategy strings explicitly."""
    return SignalResponse(
        id=sig.id,
        timestamp=sig.timestamp,
        symbol=sym_name,
        strategy=strat_name,
        signal_type=sig.signal_type,
        entry_price=float(sig.entry_price) if sig.entry_price is not None else None,
        stop_loss=float(sig.stop_loss) if sig.stop_loss is not None else None,
        target_price=float(sig.target_price) if sig.target_price is not None else None,
        quantity=sig.quantity,
        risk_amount=float(sig.risk_amount) if sig.risk_amount is not None else None,
        risk_pct=float(sig.risk_pct) if sig.risk_pct is not None else None,
        risk_reward=float(sig.risk_reward) if sig.risk_reward is not None else None,
        risk_status=sig.risk_status,
        block_reason=sig.block_reason,
        atr_pct=float(sig.atr_pct) if sig.atr_pct is not None else None,
    )


async def _fetch_signals_with_names(
    db: AsyncSession,
    base_query,
    page: int,
    page_size: int,
) -> tuple[int, list[SignalResponse]]:
    """Count total and fetch paginated signals, joining symbol + strategy names."""
    # Total count
    count_q = select(func.count()).select_from(base_query.subquery())
    total = (await db.execute(count_q)).scalar() or 0

    # Paginate and JOIN symbol + strategy names in one query
    paged_q = (
        base_query
        .add_columns(Symbol.trading_symbol, Strategy.name.label("strategy_name"))
        .outerjoin(Symbol, Symbol.id == Signal.symbol_id)
        .outerjoin(Strategy, Strategy.id == Signal.strategy_id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = (await db.execute(paged_q)).all()

    results = [
        _build_signal_response(row[0], row[1], row[2])
        for row in rows
    ]
    return total, results


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
    """Fetch paginated signals with filters — symbol and strategy names are resolved via JOIN."""
    query = select(Signal).order_by(Signal.timestamp.desc())

    if status and status.upper() != "ALL":
        query = query.where(Signal.risk_status == status.upper())
    if strategy_id:
        query = query.where(Signal.strategy_id == strategy_id)
    if from_date:
        query = query.where(func.date(Signal.timestamp) >= from_date)
    if to_date:
        query = query.where(func.date(Signal.timestamp) <= to_date)

    total, signals = await _fetch_signals_with_names(db, query, page, page_size)

    return SignalListResponse(
        total=total,
        page_size=page_size,
        signals=signals,
    )

@router.get("/signals/history", response_model=SignalListResponse)
async def signal_history(
    symbol: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    limit: int = Query(500, le=1000),
    db: AsyncSession = Depends(get_session),
    _user: dict = Depends(get_current_user),
):
    """Fetch historical signals explicitly bounded by time for frontend backfills."""
    from datetime import datetime
    
    query = select(Signal).order_by(Signal.timestamp.desc())

    if symbol:
        query = query.join(Symbol, Symbol.id == Signal.symbol_id).where(Symbol.trading_symbol == symbol)
        
    if start_time:
        try:
            st = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
            query = query.where(Signal.timestamp >= st)
        except ValueError:
            pass
            
    if end_time:
        try:
            et = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
            query = query.where(Signal.timestamp <= et)
        except ValueError:
            pass

    total, signals = await _fetch_signals_with_names(db, query, 1, limit)

    return SignalListResponse(
        total=total,
        page=1,
        page_size=limit,
        signals=signals,
    )
