"""Trades router — Full trade journal implementation."""
from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_session
from app.models.audit_log import AuditLog
from app.models.trade import Trade
from app.schemas.trade import TradeCreate, TradeListResponse, TradeResponse, TradeSummary

router = APIRouter()


@router.get("/trades", response_model=TradeListResponse)
async def list_trades(
    from_date: date | None = None,
    to_date: date | None = None,
    symbol: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_session),
    _user: dict = Depends(get_current_user),
):
    """Fetch paginated trade journal entries."""
    query = select(Trade).where(Trade.is_deleted == False).order_by(Trade.entry_time.desc())

    if from_date:
        query = query.where(func.date(Trade.entry_time) >= from_date)
    if to_date:
        query = query.where(func.date(Trade.entry_time) <= to_date)

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    # Paginate
    query = query.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    trades = result.scalars().all()

    return TradeListResponse(
        total=total,
        page=page,
        page_size=page_size,
        trades=[TradeResponse.model_validate(t) for t in trades],
    )


@router.post("/trades", response_model=TradeResponse, status_code=201)
async def create_trade(
    data: TradeCreate,
    db: AsyncSession = Depends(get_session),
    _user: dict = Depends(get_current_user),
):
    """Log a trade outcome to the journal."""
    # Calculate P&L
    if data.exit_price and data.entry_price:
        if data.direction == "LONG":
            gross_pnl = (data.exit_price - data.entry_price) * data.quantity
        else:
            gross_pnl = (data.entry_price - data.exit_price) * data.quantity
        net_pnl = gross_pnl - (data.brokerage or 0)
    else:
        gross_pnl = None
        net_pnl = None

    trade = Trade(
        signal_id=data.signal_id,
        symbol_id=data.symbol_id,
        direction=data.direction,
        entry_price=data.entry_price,
        exit_price=data.exit_price,
        quantity=data.quantity,
        entry_time=data.entry_time or datetime.now(UTC),
        exit_time=data.exit_time,
        gross_pnl=gross_pnl,
        net_pnl=net_pnl,
        brokerage=data.brokerage,
        notes=data.notes,
    )
    db.add(trade)

    # Audit log
    audit = AuditLog(
        event_type="TRADE_LOGGED",
        entity_type="trades",
        payload={
            "direction": data.direction,
            "entry_price": float(data.entry_price) if data.entry_price else None,
            "exit_price": float(data.exit_price) if data.exit_price else None,
            "net_pnl": float(net_pnl) if net_pnl else None,
        },
    )
    db.add(audit)
    await db.flush()
    await db.refresh(trade)
    return trade


@router.put("/trades/{trade_id}", response_model=TradeResponse)
async def update_trade(
    trade_id: int,
    data: TradeCreate,
    db: AsyncSession = Depends(get_session),
    _user: dict = Depends(get_current_user),
):
    """Update a trade entry."""
    result = await db.execute(
        select(Trade).where(Trade.id == trade_id, Trade.is_deleted == False)
    )
    trade = result.scalar_one_or_none()
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")

    update_fields = data.model_dump(exclude_unset=True)
    for field, value in update_fields.items():
        setattr(trade, field, value)

    # Recalculate P&L
    if trade.exit_price and trade.entry_price:
        if trade.direction == "LONG":
            trade.gross_pnl = (float(trade.exit_price) - float(trade.entry_price)) * trade.quantity
        else:
            trade.gross_pnl = (float(trade.entry_price) - float(trade.exit_price)) * trade.quantity
        trade.net_pnl = float(trade.gross_pnl) - float(trade.brokerage or 0)

    await db.flush()
    await db.refresh(trade)
    return trade


@router.delete("/trades/{trade_id}")
async def delete_trade(
    trade_id: int,
    db: AsyncSession = Depends(get_session),
    _user: dict = Depends(get_current_user),
):
    """Soft-delete a trade entry."""
    result = await db.execute(
        select(Trade).where(Trade.id == trade_id, Trade.is_deleted == False)
    )
    trade = result.scalar_one_or_none()
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")

    trade.is_deleted = True
    await db.flush()
    return {"message": "Trade deleted"}


@router.get("/trades/summary", response_model=TradeSummary)
async def trade_summary(
    period: str = Query("today", description="today / week / month / all"),
    db: AsyncSession = Depends(get_session),
    _user: dict = Depends(get_current_user),
):
    """P&L summary grouped by period."""
    query = select(Trade).where(Trade.is_deleted == False)

    today = date.today()
    if period == "today":
        query = query.where(func.date(Trade.entry_time) == today)
    elif period == "week":
        from datetime import timedelta
        week_start = today - timedelta(days=today.weekday())
        query = query.where(func.date(Trade.entry_time) >= week_start)
    elif period == "month":
        query = query.where(
            func.extract("month", Trade.entry_time) == today.month,
            func.extract("year", Trade.entry_time) == today.year,
        )

    result = await db.execute(query)
    trades = result.scalars().all()

    total_trades = len(trades)
    winners = [t for t in trades if t.net_pnl and float(t.net_pnl) > 0]
    losers = [t for t in trades if t.net_pnl and float(t.net_pnl) < 0]
    net_pnl = sum(float(t.net_pnl or 0) for t in trades)
    win_rate = (len(winners) / total_trades * 100) if total_trades > 0 else 0

    return TradeSummary(
        period=period,
        total_trades=total_trades,
        winners=len(winners),
        losers=len(losers),
        net_pnl=round(net_pnl, 2),
        win_rate=round(win_rate, 1),
        avg_win=round(sum(float(t.net_pnl) for t in winners) / len(winners), 2) if winners else 0,
        avg_loss=round(sum(float(t.net_pnl) for t in losers) / len(losers), 2) if losers else 0,
    )
