"""Symbols router — Watchlist management."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_session
from app.models.symbol import Symbol
from app.schemas.symbol import SymbolCreate, SymbolResponse

router = APIRouter()


@router.get("/symbols", response_model=list[SymbolResponse])
async def list_symbols(
    db: AsyncSession = Depends(get_session),
    _user: dict = Depends(get_current_user),
):
    """List all watchlist symbols."""
    result = await db.execute(select(Symbol).order_by(Symbol.trading_symbol))
    symbols = result.scalars().all()
    return symbols


@router.post("/symbols", response_model=SymbolResponse, status_code=status.HTTP_201_CREATED)
async def add_symbol(
    data: SymbolCreate,
    db: AsyncSession = Depends(get_session),
    _user: dict = Depends(get_current_user),
):
    """Add a symbol to the watchlist."""
    # Check for duplicate
    existing = await db.execute(
        select(Symbol).where(Symbol.trading_symbol == data.trading_symbol.upper())
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Symbol {data.trading_symbol} already exists",
        )

    # Check watchlist max (20 symbols in MVP)
    count_result = await db.execute(select(Symbol).where(Symbol.is_active is True))
    if len(count_result.scalars().all()) >= 20:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Maximum of 20 symbols allowed in MVP",
        )

    symbol = Symbol(
        trading_symbol=data.trading_symbol.upper(),
        exchange=data.exchange.upper(),
    )
    db.add(symbol)
    await db.flush()
    await db.refresh(symbol)
    return symbol


@router.delete("/symbols/{symbol_id}")
async def remove_symbol(
    symbol_id: int,
    db: AsyncSession = Depends(get_session),
    _user: dict = Depends(get_current_user),
):
    """Soft-deactivate a symbol from the watchlist."""
    result = await db.execute(select(Symbol).where(Symbol.id == symbol_id))
    symbol = result.scalar_one_or_none()
    if not symbol:
        raise HTTPException(status_code=404, detail="Symbol not found")

    symbol.is_active = False
    await db.flush()
    return {"message": "Symbol deactivated"}
