"""Paper Trading Module — Virtual positions and balance tracking."""
from typing import List
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func

from app.api.deps import get_current_user, get_db
from app.models.paper_trade import PaperTrade
from app.models.risk_config import RiskConfig
from pydantic import BaseModel, ConfigDict

router = APIRouter()

_UTC = timezone.utc


class PaperTradeCreate(BaseModel):
    symbol: str
    instrument_key: str
    direction: str
    quantity: int
    entry_price: float
    stop_loss: float
    target_price: float


class PaperTradeResponse(BaseModel):
    id: int
    symbol: str
    instrument_key: str | None
    direction: str
    quantity: int
    entry_price: float
    stop_loss: float
    target_price: float
    status: str
    exit_price: float | None
    realized_pnl: float | None
    created_at: datetime
    closed_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


def _margin_required(quantity: int, entry_price: float) -> float:
    """Intraday margin estimate: ~20 % of notional (5x leverage)."""
    return (quantity * entry_price) / 5


@router.get("/balance")
async def get_paper_balance(
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),   # FIX #1: auth added
):
    """Fetch the current virtual paper balance."""
    config_result = await db.execute(select(RiskConfig).limit(1))
    config = config_result.scalar_one_or_none()

    if not config:
        config = RiskConfig()
        db.add(config)
        await db.commit()
        await db.refresh(config)
    return {"paper_balance": config.paper_balance}


@router.get("/positions", response_model=List[PaperTradeResponse])
async def get_active_positions(
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),   # FIX #1
):
    """Fetch all open paper trades."""
    result = await db.execute(
        select(PaperTrade).where(PaperTrade.status == "OPEN").order_by(PaperTrade.created_at.desc())
    )
    return result.scalars().all()


@router.get("/history", response_model=List[PaperTradeResponse])
async def get_closed_positions(
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),   # FIX #1
):
    """Fetch all closed paper trades."""
    result = await db.execute(
        select(PaperTrade)
        .where(PaperTrade.status == "CLOSED")
        .order_by(PaperTrade.closed_at.desc())
        .limit(100)
    )
    return result.scalars().all()


@router.post("/execute", response_model=PaperTradeResponse)
async def execute_paper_trade(
    trade: PaperTradeCreate,
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),   # FIX #1
):
    """Open a new virtual trade."""
    if trade.direction not in ("BUY", "SELL"):
        raise HTTPException(status_code=400, detail="Invalid direction")

    config_result = await db.execute(select(RiskConfig).limit(1))
    config = config_result.scalar_one_or_none()

    if not config:
        config = RiskConfig()
        db.add(config)

    # FIX #7: deduct margin on open; returned on close
    margin_req = _margin_required(trade.quantity, trade.entry_price)
    if float(config.paper_balance) < margin_req:
        raise HTTPException(
            status_code=400,
            detail=f"Insufficient virtual balance. Required: ₹{margin_req:.2f}, Available: ₹{config.paper_balance:.2f}",
        )

    # Deduct margin from balance while trade is open
    config.paper_balance = float(config.paper_balance) - margin_req

    new_trade = PaperTrade(
        symbol=trade.symbol,
        instrument_key=trade.instrument_key,
        direction=trade.direction,
        quantity=trade.quantity,
        entry_price=trade.entry_price,
        stop_loss=trade.stop_loss,
        target_price=trade.target_price,
        status="OPEN",
    )
    db.add(new_trade)
    await db.commit()
    await db.refresh(new_trade)
    return new_trade


@router.post("/close/{trade_id}", response_model=PaperTradeResponse)
async def close_paper_trade(
    trade_id: int,
    exit_price: float,
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),   # FIX #1
):
    """Manually close a virtual trade."""
    trade_result = await db.execute(
        select(PaperTrade).where(PaperTrade.id == trade_id, PaperTrade.status == "OPEN")
    )
    trade = trade_result.scalar_one_or_none()

    if not trade:
        raise HTTPException(status_code=404, detail="Active paper trade not found")

    trade.status = "CLOSED"
    trade.exit_price = exit_price
    trade.closed_at = datetime.now(_UTC)       # FIX #4: real datetime, not func.now()
    trade.close_reason = "MANUAL"

    # P&L calculation
    multiplier = 1 if trade.direction == "BUY" else -1
    trade.realized_pnl = (exit_price - trade.entry_price) * trade.quantity * multiplier

    # FIX #7: restore margin + add realized_pnl (margin was locked on open)
    config_result = await db.execute(select(RiskConfig).limit(1))
    config = config_result.scalar_one_or_none()
    if config:
        margin_back = _margin_required(trade.quantity, trade.entry_price)
        config.paper_balance = float(config.paper_balance) + margin_back + trade.realized_pnl

    await db.commit()
    await db.refresh(trade)
    return trade


@router.delete("/data")
async def reset_paper_trades_data(
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
):
    """Delete all paper trades and reset the paper balance to default (₹100,000)."""
    from sqlalchemy import delete
    
    # 1. Delete all trades
    await db.execute(delete(PaperTrade))
    
    # 2. Reset balance
    config_result = await db.execute(select(RiskConfig).limit(1))
    config = config_result.scalar_one_or_none()
    if config:
        config.paper_balance = 100000.0
    else:
        config = RiskConfig(paper_balance=100000.0)
        db.add(config)
        
    await db.commit()
    return {"status": "success", "message": "Paper trades cleared and balance reset."}


@router.get("/export")
async def export_paper_trades(
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
):
    """Export all paper trades to a CSV file."""
    import csv
    import io
    from fastapi.responses import StreamingResponse

    result = await db.execute(select(PaperTrade).order_by(PaperTrade.created_at.desc()))
    trades = result.scalars().all()

    output = io.StringIO()
    writer = csv.writer(output)

    # Write header
    writer.writerow([
        "ID", "Symbol", "Direction", "Quantity", 
        "Entry Price", "Stop Loss", "Target Price", 
        "Status", "Exit Price", "Realized P&L", 
        "Created At", "Closed At", "Close Reason"
    ])

    # Write rows
    for trade in trades:
        writer.writerow([
            trade.id, 
            trade.symbol, 
            trade.direction, 
            trade.quantity,
            trade.entry_price, 
            trade.stop_loss, 
            trade.target_price,
            trade.status, 
            trade.exit_price or "", 
            trade.realized_pnl,
            trade.created_at.isoformat() if trade.created_at else "", 
            trade.closed_at.isoformat() if trade.closed_at else "", 
            trade.close_reason or ""
        ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]), 
        media_type="text/csv", 
        headers={"Content-Disposition": "attachment; filename=paper_trades.csv"}
    )
