"""
QuantDSS Scheduler — APScheduler for Market Hour Jobs
Now with real EOD summary implementation.
"""
from datetime import date, datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select, func

from app.core.logging import logger

# Scheduler instance — IST timezone for NSE market hours
scheduler = AsyncIOScheduler(timezone="Asia/Kolkata")


async def reset_daily_risk_state():
    """Reset daily risk state — runs at 09:00 IST before market open."""
    logger.info("Resetting daily risk state for new trading day")
    from app.core.database import async_session_factory
    from app.models.daily_risk_state import DailyRiskState

    async with async_session_factory() as db:
        today = date.today()
        # Check if today's state already exists
        result = await db.execute(
            select(DailyRiskState).where(DailyRiskState.trade_date == today)
        )
        existing = result.scalar_one_or_none()
        if existing:
            existing.realised_pnl = 0
            existing.is_halted = False
            existing.halt_reason = None
            existing.signals_approved = 0
            existing.signals_blocked = 0
            existing.signals_skipped = 0
        else:
            # Create new daily state
            new_state = DailyRiskState(trade_date=today, realised_pnl=0)
            db.add(new_state)

        await db.commit()
        logger.info(f"Daily risk state reset for {today}")


async def start_market_session():
    """Connect broker + warm up strategies — runs at 09:14 IST."""
    logger.info("Starting market session — connecting broker and loading strategies")
    from app.ingestion.broker_manager import broker_manager
    # FIX #6: skip re-init if already connected — avoids killing the recovery task
    if broker_manager.active_broker and broker_manager.active_broker.is_connected:
        logger.info("start_market_session: broker already connected — skipping re-init")
        return
    await broker_manager.initialize_session()


async def stop_market_session():
    """Stop signal generation — runs at 15:30 IST."""
    logger.info("Market session ended — stopping signal generation")


async def send_eod_summary():
    """
    Send EOD Telegram summary — runs at 15:35 IST.
    Collects today's signal stats, trade P&L, and sends via Telegram.
    """
    logger.info("Generating end-of-day summary...")

    from app.core.database import async_session_factory
    from app.models.signal import Signal
    from app.models.trade import Trade
    from app.alerts.alert_dispatcher import AlertDispatcher

    today = date.today()
    today_str = today.strftime("%d %b %Y")

    try:
        async with async_session_factory() as db:
            # Count today's signals by status
            signals_result = await db.execute(
                select(Signal).where(func.date(Signal.created_at) == today)
            )
            signals = signals_result.scalars().all()

            total_signals = len(signals)
            approved = sum(1 for s in signals if s.risk_status == "APPROVED")
            blocked = sum(1 for s in signals if s.risk_status == "BLOCKED")
            skipped = sum(1 for s in signals if s.risk_status == "SKIPPED")

            # Count today's trades and net P&L
            trades_result = await db.execute(
                select(Trade).where(
                    func.date(Trade.entry_time) == today,
                    Trade.deleted_at.is_(None),
                )
            )
            trades = trades_result.scalars().all()
            trades_taken = len(trades)
            net_pnl = sum(float(t.net_pnl or 0) for t in trades)

        # Dispatch via AlertDispatcher
        dispatcher = AlertDispatcher()
        await dispatcher.dispatch_eod_summary(
            date=today_str,
            total_signals=total_signals,
            approved=approved,
            blocked=blocked,
            skipped=skipped,
            trades_taken=trades_taken,
            net_pnl=net_pnl,
        )

        logger.info(
            f"EOD summary sent: {total_signals} signals, "
            f"{trades_taken} trades, ₹{net_pnl:.2f} P&L"
        )

    except Exception as e:
        logger.error(f"EOD summary failed: {e}")


async def check_broker_health():
    """
    Verify broker connection every 5 min during market hours.
    FIX #9/#13: was a no-op. Now checks is_connected flag and triggers
    re-initialization if the active broker is disconnected — without making
    a redundant HTTP call (the recovery task handles Upstox pings).
    """
    from app.ingestion.broker_manager import broker_manager
    active = broker_manager.active_broker
    if active is None:
        logger.warning("check_broker_health: no broker configured — attempting init")
        await broker_manager.initialize_session()
    elif not active.is_connected:
        logger.warning(
            f"check_broker_health: {active.name} reports DISCONNECTED — re-initializing"
        )
        await broker_manager.initialize_session()
    else:
        logger.debug(f"check_broker_health: {active.name} is connected ✓")


async def order_timeout_check():
    """
    Issue 4 Fix: Cancel PENDING live orders older than 5 minutes.
    Runs every 2 minutes during market hours.
    """
    from datetime import datetime, timezone, timedelta
    import pytz
    IST = pytz.timezone("Asia/Kolkata")
    now_ist = datetime.now(IST)
    h, m = now_ist.hour, now_ist.minute
    in_market_hours = (h > 9 or (h == 9 and m >= 15)) and (h < 15 or (h == 15 and m <= 30))
    if not in_market_hours or now_ist.weekday() >= 5:
        return
    from app.core.database import async_session_factory
    from app.engine.execution_manager import ExecutionManager
    try:
        async with async_session_factory() as db:
            mgr = ExecutionManager(db)
            cancelled = await mgr.cancel_stale_pending_orders(timeout_minutes=5)
            if cancelled:
                logger.info(f"Scheduler: cancelled {cancelled} timed-out PENDING order(s)")
    except Exception as e:
        logger.error(f"order_timeout_check error: {e}")


async def order_reconciliation_check():
    """
    Issue 7b Fix: Periodic order reconciliation for missed webhooks.
    Queries the broker for all PENDING/PARTIALLY_FILLED trades
    and syncs their status. Runs every 5 minutes.
    """
    from app.core.database import async_session_factory
    from app.engine.execution_manager import ExecutionManager
    try:
        async with async_session_factory() as db:
            mgr = ExecutionManager(db)
            await mgr.reconcile_orders()
    except Exception as e:
        logger.error(f"order_reconciliation_check error: {e}")


def setup_scheduler():
    """Register all scheduled jobs."""
    # Daily state reset — before market open
    scheduler.add_job(reset_daily_risk_state, "cron", hour=9, minute=0, id="reset_daily_state")

    # Market session start — connect broker, warm up strategies
    scheduler.add_job(start_market_session, "cron", hour=9, minute=14, id="start_session")

    # Fix C-02: Reset candle aggregator session at 09:15 IST (market open)
    async def _reset_candle_session():
        try:
            from app.ingestion.candle_aggregator import candle_aggregator
            candle_aggregator.reset_session()
            logger.info("Scheduler: Candle aggregator session reset for new day")
        except Exception as e:
            logger.error(f"Scheduler: candle session reset failed: {e}")

    scheduler.add_job(_reset_candle_session, "cron", hour=9, minute=15, id="candle_session_reset")

    # Fix C-02: Auto Square-Off at 15:15 IST — close all open positions    # Auto-square off is now handled by TradeMonitorWorker.top_market_session, "cron", hour=15, minute=30, id="stop_session")

    # Market session end — stop signal generation
    scheduler.add_job(stop_market_session, "cron", hour=15, minute=30, id="stop_session")

    # EOD summary — send Telegram daily report at 3:35 PM IST
    scheduler.add_job(send_eod_summary, "cron", hour=15, minute=35, id="eod_summary")

    # Health check — verify broker still connected every 5 minutes during market hours
    scheduler.add_job(
        check_broker_health,
        "interval",
        minutes=5,
        id="broker_health",
    )

    # Paper Trading Monitor — now runs every 15 seconds for faster SL/TP detection
    from app.engine.paper_monitor import check_paper_trades
    scheduler.add_job(
        check_paper_trades,
        "interval",
        seconds=15,
        id="paper_trade_monitor"
    )

    # Auto-Trader Engine — scan watchlist and open positions automatically
    from app.engine.auto_trader_engine import run_auto_trader
    scheduler.add_job(
        run_auto_trader,
        "interval",
        minutes=5,
        id="auto_trader",
        max_instances=1,         # never run two scans at once
    )

    # Regime Auto-Update — detect market regime and write to RiskConfig every 5 min
    from app.engine.regime_scheduler import update_regime_in_db
    scheduler.add_job(
        update_regime_in_db,
        "interval",
        minutes=5,
        id="regime_update",
    )

    # Order timeout check — cancel PENDING orders older than 5 min
    scheduler.add_job(
        order_timeout_check,
        "interval",
        minutes=2,
        id="order_timeout",
    )

    # Order reconciliation — sync missed webhooks every 5 min
    scheduler.add_job(
        order_reconciliation_check,
        "interval",
        minutes=5,
        id="order_reconciliation",
    )

    # Ensure auto-trader DB tables exist
    from app.models import auto_trade_config, auto_trade_log  # noqa: F401

    logger.info("Scheduler configured with market hour jobs (IST timezone)")

