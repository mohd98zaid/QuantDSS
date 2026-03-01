"""Risk router — Risk configuration and state."""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_session
from app.models.daily_risk_state import DailyRiskState
from app.models.risk_config import RiskConfig
from app.schemas.risk import RiskConfigResponse, RiskConfigUpdate, RiskStateResponse

router = APIRouter()


@router.get("/risk/config", response_model=RiskConfigResponse)
async def get_risk_config(
    db: AsyncSession = Depends(get_session),
    _user: dict = Depends(get_current_user),
):
    """Get current risk configuration."""
    result = await db.execute(select(RiskConfig))
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="Risk config not found. Run seed script first.")
    return config


@router.put("/risk/config", response_model=RiskConfigResponse)
async def update_risk_config(
    data: RiskConfigUpdate,
    db: AsyncSession = Depends(get_session),
    _user: dict = Depends(get_current_user),
):
    """Update risk configuration."""
    result = await db.execute(select(RiskConfig))
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="Risk config not found")

    update_data = data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(config, field, value)

    await db.flush()
    await db.refresh(config)
    return config


@router.get("/risk/state", response_model=RiskStateResponse)
async def get_risk_state(
    db: AsyncSession = Depends(get_session),
    _user: dict = Depends(get_current_user),
):
    """Get today's risk state."""
    today = date.today()
    result = await db.execute(
        select(DailyRiskState).where(DailyRiskState.trade_date == today)
    )
    state = result.scalar_one_or_none()
    if not state:
        raise HTTPException(status_code=404, detail="No risk state for today. System may not have started.")

    config_result = await db.execute(select(RiskConfig))
    config = config_result.scalar_one_or_none()

    remaining_buffer = float(state.max_daily_loss or 0) + float(state.realised_pnl or 0)

    return RiskStateResponse(
        trade_date=state.trade_date,
        account_balance=float(state.account_balance) if state.account_balance else None,
        max_daily_loss=float(state.max_daily_loss) if state.max_daily_loss else None,
        realised_pnl=float(state.realised_pnl or 0),
        remaining_daily_buffer=max(0, remaining_buffer),
        trades_taken=state.trades_taken or 0,
        signals_approved=state.signals_approved or 0,
        signals_blocked=state.signals_blocked or 0,
        signals_skipped=state.signals_skipped or 0,
        is_halted=state.is_halted or False,
        halt_reason=state.halt_reason,
        max_concurrent_positions=config.max_concurrent_positions if config else 2,
    )
