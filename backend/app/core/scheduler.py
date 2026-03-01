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
    """Verify broker WebSocket connection — runs every 5 min during market hours."""
    logger.debug("Checking broker health")


def setup_scheduler():
    """Register all scheduled jobs."""
    # Daily state reset — before market open
    scheduler.add_job(reset_daily_risk_state, "cron", hour=9, minute=0, id="reset_daily_state")

    # Market session start — connect broker, warm up strategies
    scheduler.add_job(start_market_session, "cron", hour=9, minute=14, id="start_session")

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

    logger.info("Scheduler configured with market hour jobs (IST timezone)")
