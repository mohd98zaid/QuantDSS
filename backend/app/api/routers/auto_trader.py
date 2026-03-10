"""Auto-Trader REST API — configure, enable/disable, view logs, and emergency controls."""
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status as http_status
from pydantic import BaseModel, field_validator
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.api.deps import get_current_user, get_db
from app.core.rate_limit import limiter
from app.engine.trading_mode import TradingModeController, TradingMode, is_live_lock_set
from app.models.auto_trade_config import AutoTradeConfig
from app.models.auto_trade_log import AutoTradeLog

router = APIRouter()
_UTC = timezone.utc


# ── Schemas ──────────────────────────────────────────────────────────────────

class AutoTradeConfigSchema(BaseModel):
    enabled: bool = False
    mode: str = "paper"
    sizing_mode: str = "capital"
    qty_per_trade: int = 1
    capital_per_trade: float = 10000.0
    max_open_positions: int = 3
    strategy: str = "ema_crossover"
    timeframe: str = "5min"
    watchlist: List[str] = []
    scan_interval_minutes: int = 5

    class Config:
        from_attributes = True


class AutoTradeLogSchema(BaseModel):
    id: int
    timestamp: datetime
    symbol: Optional[str] = None
    signal: Optional[str] = None
    action: str
    reason: Optional[str] = None
    entry_price: Optional[float] = None
    stop_loss: Optional[float] = None
    target_price: Optional[float] = None
    risk_reward: Optional[float] = None
    rsi: Optional[float] = None
    trend: Optional[str] = None
    strategy: Optional[str] = None
    timeframe: Optional[str] = None
    trade_id: Optional[int] = None
    trading_mode: Optional[str] = None  # TradingModeController mode tag

    class Config:
        from_attributes = True


class TradingModeRequest(BaseModel):
    """Request body for switching trading mode."""
    mode: str  # "disabled", "paper", or "live"
    confirm_live: bool = False  # Must be True when switching to LIVE


class TradingModeResponse(BaseModel):
    """Response for trading mode queries and switches."""
    current_mode: str
    db_mode: str
    enabled: bool
    live_lock_set: bool
    live_available: bool
    safety_downgraded: bool
    message: str = ""


# ── Helpers ──────────────────────────────────────────────────────────────────

async def _get_or_create_config(db: AsyncSession) -> AutoTradeConfig:
    result = await db.execute(select(AutoTradeConfig).limit(1))
    cfg = result.scalar_one_or_none()
    if not cfg:
        cfg = AutoTradeConfig()
        db.add(cfg)
        await db.commit()
        await db.refresh(cfg)
    return cfg


# ── Standard Endpoints ─────────────────────────────────────────────────────────

@router.get("/auto-trader/config", response_model=AutoTradeConfigSchema)
async def get_config(
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
):
    """Return current auto-trader configuration."""
    return await _get_or_create_config(db)


@router.put("/auto-trader/config", response_model=AutoTradeConfigSchema)
@limiter.limit("10/minute")
async def update_config(
    request: Request,
    payload: AutoTradeConfigSchema,
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
):
    """Update auto-trader configuration (also enables/disables it)."""
    cfg = await _get_or_create_config(db)

    # Validate mode via TradingModeController
    req_mode = payload.mode.lower() if payload.mode else "paper"
    is_valid, err = TradingModeController.validate_mode_switch(req_mode)
    if not is_valid:
        raise HTTPException(status_code=http_status.HTTP_403_FORBIDDEN, detail=err)

    cfg.enabled = payload.enabled
    cfg.mode = req_mode
    cfg.sizing_mode = payload.sizing_mode if payload.sizing_mode in ("capital", "quantity") else "capital"
    cfg.qty_per_trade = max(1, payload.qty_per_trade)
    cfg.capital_per_trade = max(100.0, payload.capital_per_trade)
    cfg.max_open_positions = max(1, min(10, payload.max_open_positions))
    cfg.strategy = payload.strategy
    cfg.timeframe = payload.timeframe
    cfg.watchlist = [s.upper().strip() for s in payload.watchlist if s.strip()]
    cfg.scan_interval_minutes = max(1, payload.scan_interval_minutes)
    await db.commit()
    await db.refresh(cfg)
    return cfg


@router.get("/auto-trader/log", response_model=List[AutoTradeLogSchema])
async def get_log(
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
):
    """Return the last N auto-trader log entries, newest first."""
    result = await db.execute(
        select(AutoTradeLog).order_by(AutoTradeLog.timestamp.desc()).limit(min(limit, 200))
    )
    return result.scalars().all()


@router.post("/auto-trader/trigger")
@limiter.limit("10/minute")
async def manual_trigger(
    request: Request,
    _user: dict = Depends(get_current_user),
):
    """Manually trigger one auto-trader scan cycle (for testing)."""
    from app.engine.auto_trader_engine import run_auto_trader
    import asyncio
    asyncio.create_task(run_auto_trader())
    return {"status": "triggered", "message": "Auto-trader scan started in background"}


@router.delete("/auto-trader/data")
async def reset_auto_trader_data(
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
):
    """Delete all auto-trader logs."""
    from sqlalchemy import delete
    await db.execute(delete(AutoTradeLog))
    await db.commit()
    return {"status": "success", "message": "Auto-trader logs cleared."}


# ── Trading Mode Endpoints ───────────────────────────────────────────────────

@router.get("/auto-trader/trading-mode", response_model=TradingModeResponse)
async def get_trading_mode(
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
):
    """
    Return the current effective trading mode and safety lock status.

    The `current_mode` reflects the actual execution route (may differ from
    `db_mode` if LIVE was requested but the env-var lock is not set, in which
    case `safety_downgraded=true` and `current_mode` will be "paper").
    """
    cfg = await _get_or_create_config(db)
    status = TradingModeController.get_status(cfg)
    return TradingModeResponse(**status)


@router.post("/auto-trader/trading-mode", response_model=TradingModeResponse)
@limiter.limit("10/minute")
async def set_trading_mode(
    request: Request,
    payload: TradingModeRequest,
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
):
    """
    Switch the active trading mode.

    - **DISABLED** — All signals are dropped after Risk Engine. No trades created.
    - **PAPER** — Simulated trades in PaperTrade table. No broker call.
    - **LIVE** — Real orders via ExecutionManager. Requires:
        1. `confirm_live: true` in request body
        2. `LIVE_TRADING_LOCK=CONFIRMED` env var on the server

    The safety lock ensures LIVE trading can never be enabled from the UI alone.
    """
    from app.core.logging import logger

    req_mode = payload.mode.lower()

    # Validate via TradingModeController
    is_valid, err = TradingModeController.validate_mode_switch(req_mode)
    if not is_valid:
        raise HTTPException(status_code=http_status.HTTP_403_FORBIDDEN, detail=err)

    # LIVE mode requires explicit confirmation in request body
    if req_mode == TradingMode.LIVE and not payload.confirm_live:
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail=(
                "Switching to LIVE mode requires confirm_live=true in the request body. "
                "This is a deliberate safety confirmation step."
            ),
        )

    cfg = await _get_or_create_config(db)
    old_mode = cfg.mode
    cfg.mode = req_mode
    if req_mode == TradingMode.DISABLED:
        cfg.enabled = False  # DISABLED mode also disables the scheduler
    elif req_mode in (TradingMode.PAPER, TradingMode.LIVE):
        cfg.enabled = True   # Switching to an active mode re-enables the trader
    await db.commit()
    await db.refresh(cfg)

    logger.warning(
        f"TradingMode: mode switched from '{old_mode}' to '{req_mode}' by operator"
    )

    # Send notification for LIVE mode switch
    if req_mode == TradingMode.LIVE:
        import asyncio
        try:
            from app.core.notifier import notifier
            asyncio.create_task(notifier.send_alert(
                title="⚠️ LIVE Trading Mode Activated",
                message=(
                    "Trading mode switched to LIVE. "
                    "Real orders will now be sent to the broker."
                ),
                level="CRITICAL",
            ))
        except Exception:
            pass

    status = TradingModeController.get_status(cfg)
    return TradingModeResponse(
        **status,
        message=f"Trading mode changed from '{old_mode}' to '{req_mode}'",
    )


# ── Emergency Safety Endpoints (Audit Cat. 14) ─────────────────────────────────

@router.post("/auto-trader/halt")
@limiter.limit("20/minute")
async def emergency_halt(
    request: Request,
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
):
    """
    Emergency halt — immediately disables the auto-trader and marks today's
    DailyRiskState as halted. Switches trading mode to DISABLED.
    No new trades will be opened until /resume is called.
    """
    import asyncio
    from datetime import date, datetime, timezone, timedelta
    from app.models.daily_risk_state import DailyRiskState
    from app.core.logging import logger

    IST = timezone(timedelta(hours=5, minutes=30))
    cfg = await _get_or_create_config(db)
    old_mode = cfg.mode
    cfg.enabled = False
    cfg.mode = TradingMode.DISABLED  # Switch to DISABLED mode on halt

    today = date.today()
    result = await db.execute(
        select(DailyRiskState).where(DailyRiskState.trade_date == today)
    )
    state = result.scalar_one_or_none()
    if state:
        state.is_halted = True
        state.halt_reason = "MANUAL_EMERGENCY_HALT"
        state.halt_triggered_at = datetime.now(IST)
    else:
        db.add(DailyRiskState(
            trade_date=today, is_halted=True,
            halt_reason="MANUAL_EMERGENCY_HALT",
            halt_triggered_at=datetime.now(IST),
        ))

    await db.commit()
    logger.warning(
        f"TradingMode: EMERGENCY HALT — mode switched '{old_mode}' → 'disabled'"
    )
    asyncio.create_task(notifier.send_alert(
        title="🚨 EMERGENCY HALT ACTIVATED",
        message="Auto-trader manually halted. Mode set to DISABLED. No new positions will be entered.",
        level="CRITICAL",
    ))
    return {
        "status": "halted",
        "trading_mode": TradingMode.DISABLED,
        "message": "Auto-trader emergency halt activated. Mode set to DISABLED. Use /resume to restore.",
    }


@router.post("/auto-trader/resume")
@limiter.limit("5/minute")
async def resume_after_halt(
    request: Request,
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
):
    """Resume trading after an emergency halt. Resets mode to PAPER (safe default)."""
    from datetime import date
    from app.models.daily_risk_state import DailyRiskState

    result = await db.execute(
        select(DailyRiskState).where(DailyRiskState.trade_date == date.today())
    )
    state = result.scalar_one_or_none()
    if state:
        state.is_halted = False
        state.halt_reason = None

    cfg = await _get_or_create_config(db)
    cfg.enabled = True
    cfg.mode = TradingMode.PAPER  # Always resume into PAPER — operator must explicitly switch to LIVE
    await db.commit()
    return {
        "status": "resumed",
        "trading_mode": TradingMode.PAPER,
        "message": "Auto-trader resumed in PAPER mode. Switch to LIVE explicitly if needed.",
    }


@router.post("/auto-trader/close-all")
@limiter.limit("5/minute")
async def kill_switch_close_all(
    request: Request,
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
):
    """
    Kill switch — force-closes ALL open paper trades at current LTP.
    Fix (Audit Cat. 14): Operator safety net for catastrophic situations.
    """
    import asyncio
    from datetime import datetime, timezone, timedelta
    from app.models.paper_trade import PaperTrade
    from app.ingestion.upstox_http import UpstoxHTTPClient
    from app.ingestion.websocket_manager import market_data_cache

    IST = timezone(timedelta(hours=5, minutes=30))
    result = await db.execute(select(PaperTrade).where(PaperTrade.status == "OPEN"))
    open_trades = result.scalars().all()

    if not open_trades:
        return {"status": "no_action", "message": "No open paper trades to close."}

    upstox = UpstoxHTTPClient()
    closed, total_pnl = 0, 0.0

    for trade in open_trades:
        ltp = market_data_cache.get_ltp(trade.instrument_key or "")
        if not ltp:
            try:
                quotes = await upstox.get_ltp([trade.instrument_key or f"NSE_EQ|{trade.symbol}"])
                ltp = next(iter(quotes.values()), None) if quotes else None
            except Exception:
                ltp = None
        exit_price = float(ltp or trade.entry_price)
        multiplier = 1 if trade.direction == "BUY" else -1
        pnl = (exit_price - trade.entry_price) * trade.quantity * multiplier
        trade.status = "CLOSED"
        trade.exit_price = exit_price
        trade.closed_at = datetime.now(IST)
        trade.close_reason = "KILL_SWITCH"
        trade.realized_pnl = pnl
        total_pnl += pnl
        closed += 1
        db.add(AutoTradeLog(
            symbol=trade.symbol, signal=trade.direction, action="CLOSE",
            reason="KILL_SWITCH", entry_price=trade.entry_price,
            stop_loss=trade.stop_loss, target_price=trade.target_price,
            trade_id=trade.id,
        ))

    await db.commit()
    from app.core.notifier import notifier
    asyncio.create_task(notifier.send_alert(
        title="KILL SWITCH EXECUTED",
        message=f"Force-closed {closed} trade(s). Net PnL: Rs.{total_pnl:.2f}",
        level="CRITICAL",
    ))
    return {"status": "closed", "trades_closed": closed, "total_pnl": round(total_pnl, 2)}


# ── Export Endpoint ────────────────────────────────────────────────────────────

@router.get("/auto-trader/export")
async def export_auto_trader_logs(
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
):
    """Export auto-trader logs to a CSV file."""
    import csv
    import io
    from fastapi.responses import StreamingResponse

    result = await db.execute(
        select(AutoTradeLog).order_by(AutoTradeLog.timestamp.desc())
    )
    logs = result.scalars().all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "ID", "Timestamp", "Symbol", "Signal", "Action",
        "Entry Price", "Stop Loss", "Target Price", "Risk Reward",
        "RSI", "Trend", "Strategy", "Timeframe", "Reason", "Trade ID"
    ])
    for log in logs:
        writer.writerow([
            log.id,
            log.timestamp.isoformat() if log.timestamp else "",
            log.symbol or "", log.signal or "", log.action or "",
            log.entry_price or "", log.stop_loss or "", log.target_price or "",
            log.risk_reward or "", log.rsi or "", log.trend or "",
            log.strategy or "", log.timeframe or "", log.reason or "",
            log.trade_id or ""
        ])
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=auto_trader_logs.csv"}
    )
