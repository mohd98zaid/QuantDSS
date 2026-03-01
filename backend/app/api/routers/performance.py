"""Performance router — Performance analytics with real data."""
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_session
from app.models.daily_risk_state import DailyRiskState
from app.models.trade import Trade

router = APIRouter()


@router.get("/performance/equity-curve")
async def equity_curve(
    days: int = Query(30, ge=7, le=365),
    db: AsyncSession = Depends(get_session),
    _user: dict = Depends(get_current_user),
):
    """Equity curve data — daily balance snapshots for charting."""
    from_date = date.today() - timedelta(days=days)

    result = await db.execute(
        select(DailyRiskState)
        .where(DailyRiskState.trade_date >= from_date)
        .order_by(DailyRiskState.trade_date.asc())
    )
    states = result.scalars().all()

    return {
        "data": [
            {
                "date": str(s.trade_date),
                "balance": float(s.account_balance or 0),
                "pnl": float(s.realised_pnl or 0),
            }
            for s in states
        ]
    }


@router.get("/performance/drawdown")
async def drawdown(
    days: int = Query(30, ge=7, le=365),
    db: AsyncSession = Depends(get_session),
    _user: dict = Depends(get_current_user),
):
    """Daily drawdown from peak balance."""
    from_date = date.today() - timedelta(days=days)

    result = await db.execute(
        select(DailyRiskState)
        .where(DailyRiskState.trade_date >= from_date)
        .order_by(DailyRiskState.trade_date.asc())
    )
    states = result.scalars().all()

    data = []
    peak = 0.0
    for s in states:
        balance = float(s.account_balance or 0)
        peak = max(peak, balance)
        dd_pct = ((peak - balance) / peak * 100) if peak > 0 else 0.0
        data.append({
            "date": str(s.trade_date),
            "drawdown_pct": round(dd_pct, 2),
            "balance": balance,
            "peak": peak,
        })

    return {"data": data}


@router.get("/performance/by-strategy")
async def by_strategy(
    db: AsyncSession = Depends(get_session),
    _user: dict = Depends(get_current_user),
):
    """Per-strategy performance breakdown from trade journal."""
    result = await db.execute(
        select(Trade).where(Trade.is_deleted == False)
    )
    trades = result.scalars().all()

    # Group by strategy (via signal_id → strategy)
    strategy_stats: dict = {}

    for t in trades:
        key = t.signal_id or 0  # Group by signal association
        if key not in strategy_stats:
            strategy_stats[key] = {
                "total": 0, "winners": 0, "losers": 0,
                "gross_pnl": 0.0, "trades": []
            }
        stats = strategy_stats[key]
        stats["total"] += 1
        pnl = float(t.net_pnl or 0)
        stats["gross_pnl"] += pnl
        if pnl > 0:
            stats["winners"] += 1
        elif pnl < 0:
            stats["losers"] += 1

    return [
        {
            "strategy_id": k,
            "total_trades": v["total"],
            "win_rate": round(v["winners"] / v["total"] * 100, 1) if v["total"] > 0 else 0,
            "net_pnl": round(v["gross_pnl"], 2),
        }
        for k, v in strategy_stats.items()
    ]
