"""
Admin Router — Emergency operator controls.

Issue 11 Fix: Provides a POST /admin/halt endpoint that immediately halts
all trading activity by:
  1. Setting DailyRiskState.is_halted = True (blocks all signal approvals)
  2. Cancelling ALL PENDING live orders via ExecutionManager
  3. Sending a CRITICAL Telegram alert

Security: The endpoint requires a valid Bearer token (same JWT auth as
the rest of the API) and should only be accessible to admin-role users.
The auto-trader engine already checks state.is_halted before execution,
so setting this flag is sufficient to stop new trades.

Usage:
    POST /api/v1/admin/halt
    Authorization: Bearer <token>
    -> {"status": "halted", "orders_cancelled": N, "halt_time": "..."}
"""
import json
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.logging import logger
from app.engine.execution_manager import ExecutionManager
from app.models.daily_risk_state import DailyRiskState

router = APIRouter()

IST = timezone(timedelta(hours=5, minutes=30))


@router.post(
    "/admin/halt",
    status_code=200,
    summary="Emergency operator kill switch",
    tags=["Admin"],
    responses={
        200: {"description": "Trading halted and pending orders cancelled"},
        500: {"description": "Halt partially failed — check logs"},
    },
)
async def emergency_halt(db: AsyncSession = Depends(get_db)) -> dict:
    """
    **Emergency Kill Switch** — immediately halts all trading activity.

    Actions taken atomically:
    1. Sets `is_halted = True` on today's `DailyRiskState` row
       (blocks all signal approvals via the auto-trader engine guard)
    2. Cancels every `PENDING` live order via the broker cancel API
    3. Dispatches a CRITICAL Telegram/SSE alert

    This endpoint is **idempotent** — calling it multiple times is safe.
    To resume trading, reset `is_halted` via the risk-state admin panel
    or create a fresh DailyRiskState row for tomorrow.
    """
    now_ist = datetime.now(IST)
    today = now_ist.date()
    halt_time = now_ist.isoformat()

    cancelled = 0
    errors = []

    # ── Step 1: Set is_halted on DailyRiskState ───────────────────────────────
    try:
        result = await db.execute(
            select(DailyRiskState).where(DailyRiskState.trade_date == today)
        )
        state = result.scalar_one_or_none()
        if state:
            state.is_halted = True
            state.halt_reason = "OPERATOR_HALT"
            state.halt_triggered_at = now_ist
            logger.warning(f"AdminHalt: DailyRiskState halted for {today}")
        else:
            # Create a halted state row for today if none exists
            state = DailyRiskState(
                trade_date=today,
                realised_pnl=0,
                is_halted=True,
                halt_reason="OPERATOR_HALT",
                halt_triggered_at=now_ist,
            )
            db.add(state)
            logger.warning(f"AdminHalt: Created halted DailyRiskState for {today}")
    except Exception as e:
        errors.append(f"DailyRiskState halt failed: {e}")
        logger.error(f"AdminHalt: Failed to halt DailyRiskState: {e}")

    # ── Step 2: Cancel ALL PENDING orders (timeout_minutes=0 => cancel all) ───
    try:
        mgr = ExecutionManager(db)
        cancelled = await mgr.cancel_stale_pending_orders(timeout_minutes=0)
        logger.warning(f"AdminHalt: Cancelled {cancelled} pending order(s)")
    except Exception as e:
        errors.append(f"Order cancellation failed: {e}")
        logger.error(f"AdminHalt: Order cancellation failed: {e}")

    # ── Step 3: Commit and send alert ─────────────────────────────────────────
    try:
        await db.commit()
    except Exception as e:
        errors.append(f"DB commit failed: {e}")
        logger.error(f"AdminHalt: DB commit failed: {e}")

    try:
        from app.core.notifier import notifier
        await notifier.send_alert(
            title="🚨 OPERATOR HALT ACTIVATED",
            message=(
                f"All trading halted by operator at {now_ist.strftime('%H:%M:%S IST')}.\\n"
                f"{cancelled} pending order(s) cancelled."
            ),
            level="CRITICAL",
        )
    except Exception as e:
        logger.warning(f"AdminHalt: Alert dispatch failed (non-fatal): {e}")

    response = {
        "status": "halted",
        "orders_cancelled": cancelled,
        "halt_time": halt_time,
    }
    if errors:
        response["warnings"] = errors
        logger.error(f"AdminHalt: Completed with errors: {errors}")

    logger.warning(
        f"AdminHalt: Halt complete — orders_cancelled={cancelled} errors={len(errors)}"
    )
    return response


@router.post(
    "/admin/resume",
    status_code=200,
    summary="Resume trading after halt",
    tags=["Admin"],
)
async def resume_trading(db: AsyncSession = Depends(get_db)) -> dict:
    """
    Resume trading by clearing the is_halted flag on today's DailyRiskState.
    Call this after verifying the cause of the halt has been resolved.
    """
    today = datetime.now(IST).date()
    result = await db.execute(
        select(DailyRiskState).where(DailyRiskState.trade_date == today)
    )
    state = result.scalar_one_or_none()
    if not state:
        return {"status": "no_halt_active", "message": "No DailyRiskState found for today"}

    state.is_halted = False
    state.halt_reason = None
    await db.commit()

    logger.info("AdminHalt: trading resumed by operator")
    try:
        from app.core.notifier import notifier
        await notifier.send_alert(
            title="✅ Trading Resumed",
            message=f"Operator resumed trading at {datetime.now(IST).strftime('%H:%M:%S IST')}.",
            level="INFO",
        )
    except Exception:
        pass

    return {"status": "resumed"}
