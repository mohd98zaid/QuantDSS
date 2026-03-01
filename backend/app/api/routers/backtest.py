"""Backtest router — Run backtests and view results."""

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_session
from app.engine.backtest_engine import BacktestEngine
from app.engine.risk_engine import RiskEngine
from app.engine.strategies.ema_crossover import EMACrossoverStrategy
from app.engine.strategies.rsi_mean_reversion import RSIMeanReversionStrategy
from app.models.backtest_run import BacktestRun
from app.models.backtest_run import BacktestTrade as BacktestTradeModel
from app.models.candle import Candle
from app.models.strategy import Strategy
from app.models.symbol import Symbol
from app.schemas.backtest import BacktestCreate, BacktestResponse

router = APIRouter()

STRATEGY_MAP = {
    "trend_following": EMACrossoverStrategy,
    "mean_reversion": RSIMeanReversionStrategy,
}


@router.post("/backtest/run", response_model=BacktestResponse)
async def run_backtest(
    data: BacktestCreate,
    db: AsyncSession = Depends(get_session),
    _user: dict = Depends(get_current_user),
):
    """Run a backtest and store results."""
    # Get strategy
    result = await db.execute(select(Strategy).where(Strategy.id == data.strategy_id))
    strategy_model = result.scalar_one_or_none()
    if not strategy_model:
        raise HTTPException(status_code=404, detail="Strategy not found")

    # Get symbol
    result = await db.execute(select(Symbol).where(Symbol.id == data.symbol_id))
    symbol_model = result.scalar_one_or_none()
    if not symbol_model:
        raise HTTPException(status_code=404, detail="Symbol not found")

    # Fetch candles from DB
    candle_query = (
        select(Candle)
        .where(Candle.symbol_id == data.symbol_id)
        .where(Candle.timeframe == (data.timeframe or "1d"))
        .order_by(Candle.time.asc())
    )
    if data.start_date:
        candle_query = candle_query.where(Candle.time >= data.start_date)
    if data.end_date:
        candle_query = candle_query.where(Candle.time <= data.end_date)

    candle_result = await db.execute(candle_query)
    candles = candle_result.scalars().all()

    if len(candles) < 60:
        raise HTTPException(
            status_code=400,
            detail=f"Insufficient candle data: {len(candles)} (need at least 60)"
        )

    # Convert to DataFrame
    df = pd.DataFrame([{
        "time": c.time,
        "open": float(c.open),
        "high": float(c.high),
        "low": float(c.low),
        "close": float(c.close),
        "volume": c.volume,
    } for c in candles])

    # Create strategy instance
    strategy_cls = STRATEGY_MAP.get(strategy_model.type)
    if not strategy_cls:
        raise HTTPException(status_code=400, detail=f"Unknown strategy type: {strategy_model.type}")

    params = data.params_override or strategy_model.parameters or {}
    strategy = strategy_cls(strategy_id=strategy_model.id, params=params)

    # Create risk config mock
    from app.models.risk_config import RiskConfig
    config_result = await db.execute(select(RiskConfig))
    risk_config = config_result.scalar_one_or_none()
    if not risk_config:
        raise HTTPException(status_code=400, detail="Risk config not found. Run seed first.")

    # Run backtest
    risk_engine = RiskEngine(risk_config)
    engine = BacktestEngine(
        strategy=strategy,
        risk_engine=risk_engine,
        initial_balance=data.initial_capital or 100_000.0,
    )
    bt_result = engine.run(df, symbol_model.trading_symbol, symbol_model.id)

    # Store results in DB
    run = BacktestRun(
        strategy_id=strategy_model.id,
        symbol_id=symbol_model.id,
        start_date=df["time"].iloc[0] if len(df) > 0 else None,
        end_date=df["time"].iloc[-1] if len(df) > 0 else None,
        initial_capital=bt_result.initial_balance,
        total_return_pct=bt_result.total_return_pct,
        total_trades=bt_result.total_trades,
        winning_trades=bt_result.winning_trades,
        losing_trades=bt_result.losing_trades,
        win_rate_pct=bt_result.win_rate,
        max_drawdown_pct=bt_result.max_drawdown_pct,
        sharpe_ratio=bt_result.sharpe_ratio,
        profit_factor=bt_result.profit_factor,
        parameters=params,
    )
    db.add(run)
    await db.flush()

    # Store individual trades
    for trade in bt_result.trades:
        bt_trade = BacktestTradeModel(
            run_id=run.id,
            direction=trade.signal_type,
            entry_time=trade.entry_time,
            exit_time=trade.exit_time,
            entry_price=trade.entry_price,
            exit_price=trade.exit_price,
            quantity=trade.quantity,
            net_pnl=trade.pnl,
            exit_reason=trade.exit_reason,
        )
        db.add(bt_trade)

    await db.flush()
    await db.refresh(run)

    return BacktestResponse(
        id=run.id,
        strategy_name=strategy_model.name,
        symbol=symbol_model.trading_symbol,
        timeframe=data.timeframe or "1d",
        start_date=str(bt_result.start_date),
        end_date=str(bt_result.end_date),
        initial_capital=bt_result.initial_balance,
        final_capital=bt_result.final_balance,
        total_return_pct=bt_result.total_return_pct,
        total_trades=bt_result.total_trades,
        winning_trades=bt_result.winning_trades,
        losing_trades=bt_result.losing_trades,
        win_rate=bt_result.win_rate,
        max_drawdown_pct=bt_result.max_drawdown_pct,
        sharpe_ratio=bt_result.sharpe_ratio,
        profit_factor=bt_result.profit_factor,
    )


@router.get("/backtest/runs")
async def list_backtest_runs(
    db: AsyncSession = Depends(get_session),
    _user: dict = Depends(get_current_user),
):
    """List all past backtest runs."""
    result = await db.execute(
        select(BacktestRun).order_by(BacktestRun.created_at.desc()).limit(50)
    )
    runs = result.scalars().all()
    return [
        {
            "id": r.id,
            "strategy_id": r.strategy_id,
            "symbol_id": r.symbol_id,
            "timeframe": "1d",  # Omitted from DB to match schema, returning default
            "total_return_pct": float(r.total_return_pct) if r.total_return_pct else 0,
            "win_rate": float(r.win_rate_pct) if r.win_rate_pct else 0,
            "total_trades": r.total_trades,
            "sharpe_ratio": float(r.sharpe_ratio) if r.sharpe_ratio else 0,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in runs
    ]


@router.get("/backtest/runs/{run_id}")
async def get_backtest_results(
    run_id: int,
    db: AsyncSession = Depends(get_session),
    _user: dict = Depends(get_current_user),
):
    """Get full backtest results including trades."""
    result = await db.execute(select(BacktestRun).where(BacktestRun.id == run_id))
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="Backtest run not found")

    trades_result = await db.execute(
        select(BacktestTradeModel)
        .where(BacktestTradeModel.run_id == run_id)
        .order_by(BacktestTradeModel.entry_time)
    )
    trades = trades_result.scalars().all()

    return {
        "run": {
            "id": run.id,
            "total_return_pct": float(run.total_return_pct) if run.total_return_pct else 0,
            "win_rate": float(run.win_rate_pct) if run.win_rate_pct else 0,
            "total_trades": run.total_trades,
            "max_drawdown_pct": float(run.max_drawdown_pct) if run.max_drawdown_pct else 0,
            "sharpe_ratio": float(run.sharpe_ratio) if run.sharpe_ratio else 0,
            "profit_factor": float(run.profit_factor) if run.profit_factor else 0,
            "initial_capital": float(run.initial_capital) if run.initial_capital else 0,
            "final_capital": float(run.initial_capital) * (1 + float(run.total_return_pct) / 100) if run.initial_capital and run.total_return_pct else 0,
        },
        "trades": [
            {
                "signal_type": t.direction,
                "entry_time": t.entry_time.isoformat() if t.entry_time else None,
                "exit_time": t.exit_time.isoformat() if t.exit_time else None,
                "entry_price": float(t.entry_price) if t.entry_price else 0,
                "exit_price": float(t.exit_price) if t.exit_price else 0,
                "quantity": t.quantity,
                "pnl": float(t.net_pnl) if t.net_pnl else 0,
                "exit_reason": t.exit_reason,
            }
            for t in trades
        ],
    }
