"""Strategies router — Strategy configuration."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_session
from app.engine.strategy_health import strategy_health_monitor
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
    # Check if strategy exists
    result = await db.execute(select(Strategy).where(Strategy.id == strategy_id))
    strategy = result.scalar_one_or_none()
    if not strategy:
        raise HTTPException(status_code=404, detail="Strategy not found")

    mapping = StrategySymbol(
        strategy_id=strategy_id,
        symbol_id=data.symbol_id,
        timeframe=data.timeframe,
    )
    db.add(mapping)
    await db.flush()
    await db.refresh(mapping)
    return mapping


@router.get("/strategies/health")
async def get_strategy_health(
    db: AsyncSession = Depends(get_session),
    _user: dict = Depends(get_current_user),
):
    """
    Phase 2: Strategy Health Monitor.
    Returns real-time win rate, profit factor, consecutive losses,
    and disabled status for all strategies tracked by the health monitor.
    Also enriches with strategy names from the DB.
    """
    # Get all strategies for name enrichment
    result = await db.execute(select(Strategy).order_by(Strategy.name))
    strategies = {s.id: s.name for s in result.scalars().all()}

    all_metrics = strategy_health_monitor.get_all_metrics()

    return [
        {
            "strategy_id":         m.strategy_id,
            "strategy_name":       strategies.get(m.strategy_id, f"Strategy-{m.strategy_id}"),
            "win_rate_pct":        m.win_rate_pct,
            "profit_factor":       m.profit_factor,
            "avg_win":             m.avg_win,
            "avg_loss":            m.avg_loss,
            "consecutive_losses":  m.consecutive_losses,
            "total_trades":        m.total_trades,
            "is_disabled":         m.is_disabled,
            "disable_reason":      m.disable_reason,
            "paused_until":        m.paused_until.isoformat() if m.paused_until else None,
        }
        for m in all_metrics
    ]


@router.post("/strategies/{strategy_id}/health/re-enable")
async def re_enable_strategy(
    strategy_id: int,
    db: AsyncSession = Depends(get_session),
    _user: dict = Depends(get_current_user),
):
    """
    Manually re-enable a strategy that was auto-disabled by the health monitor.
    Use this after reviewing the strategy's performance.
    """
    result = await db.execute(select(Strategy).where(Strategy.id == strategy_id))
    strategy = result.scalar_one_or_none()
    if not strategy:
        raise HTTPException(status_code=404, detail="Strategy not found")

    strategy_health_monitor.re_enable(strategy_id)
    return {"status": "ok", "message": f"Strategy {strategy_id} re-enabled"}
