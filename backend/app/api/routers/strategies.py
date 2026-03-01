"""Strategies router — Strategy configuration."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_session
from app.models.strategy import Strategy, StrategySymbol
from app.schemas.strategy import StrategyResponse, StrategySymbolCreate, StrategySymbolResponse, StrategyUpdate

router = APIRouter()


@router.get("/strategies", response_model=list[StrategyResponse])
async def list_strategies(
    db: AsyncSession = Depends(get_session),
    _user: dict = Depends(get_current_user),
):
    """List all strategies."""
    result = await db.execute(select(Strategy).order_by(Strategy.name))
    return result.scalars().all()


@router.put("/strategies/{strategy_id}", response_model=StrategyResponse)
async def update_strategy(
    strategy_id: int,
    data: StrategyUpdate,
    db: AsyncSession = Depends(get_session),
    _user: dict = Depends(get_current_user),
):
    """Update strategy parameters or active status."""
    result = await db.execute(select(Strategy).where(Strategy.id == strategy_id))
    strategy = result.scalar_one_or_none()
    if not strategy:
        raise HTTPException(status_code=404, detail="Strategy not found")

    if data.is_active is not None:
        strategy.is_active = data.is_active
    if data.parameters is not None:
        strategy.parameters = data.parameters

    await db.flush()
    await db.refresh(strategy)
    return strategy


@router.post("/strategies/{strategy_id}/symbols", response_model=StrategySymbolResponse)
async def add_symbol_to_strategy(
    strategy_id: int,
    data: StrategySymbolCreate,
    db: AsyncSession = Depends(get_session),
    _user: dict = Depends(get_current_user),
):
    """Add a symbol to a strategy's watch list."""
    mapping = StrategySymbol(
        strategy_id=strategy_id,
        symbol_id=data.symbol_id,
        timeframe=data.timeframe,
    )
    db.add(mapping)
    await db.flush()
    await db.refresh(mapping)
    return mapping
