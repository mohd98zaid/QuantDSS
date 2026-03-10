import logging
import asyncio
from datetime import datetime, timezone
from sqlalchemy import select, func, and_

from app.core.database import async_session_factory
from app.system.trading_state import set_trading_state
from app.models.daily_risk_state import DailyRiskState
from app.engine.risk_engine import get_api_error_count

logger = logging.getLogger(__name__)

# Configurable thresholds
MAX_DAILY_LOSS = 50000.0  # e.g., INR 50,000 max daily system loss
MAX_API_ERRORS = 10       # Max consecutive API errors before system halt
MAX_VOLATILITY_ATR = 3.0  # Multiplier over historical ATR representing dangerous volatility


async def evaluate_daily_loss_breaker():
    """Circuit Breaker: Global Daily Loss"""
    try:
        async with async_session_factory() as db:
            result = await db.execute(
                select(DailyRiskState).where(
                    DailyRiskState.date == datetime.now(timezone.utc).date()
                )
            )
            states = result.scalars().all()
            
            total_realised_loss = 0.0
            for state in states:
                total_realised_loss += getattr(state, "realised_pnl", 0.0)
                
            if total_realised_loss < -MAX_DAILY_LOSS:
                logger.error(
                    f"[CIRCUIT BREAKER] MAX DAILY LOSS EXCEEDED (₹{abs(total_realised_loss):.2f}). "
                    f"ACTIVATING EMERGENCY FLATTEN!"
                )
                await set_trading_state("EMERGENCY_FLATTEN", "system:breaker:daily_loss", f"Loss {total_realised_loss:.2f} < {-MAX_DAILY_LOSS}", db_session=db)
                
    except Exception as e:
        logger.error(f"Failed to evaluate daily loss breaker: {e}")


async def evaluate_api_failure_breaker():
    """Circuit Breaker: Broker API Failures"""
    try:
        error_count = await get_api_error_count()
        if error_count >= MAX_API_ERRORS:
            logger.error(
                f"[CIRCUIT BREAKER] MAX API ERRORS EXCEEDED ({error_count}). "
                f"DISABLING TRADING!"
            )
            async with async_session_factory() as db:
                await set_trading_state("DISABLED", "system:breaker:api_failure", f"{error_count} consecutive API errors", db_session=db)
    except Exception as e:
        logger.error(f"Failed to evaluate API failure breaker: {e}")


async def evaluate_volatility_breaker(current_atr: float, historical_atr_baseline: float):
    """
    Circuit Breaker: Extreme Volatility
    Called directly by signal engine or market data processors.
    """
    if historical_atr_baseline <= 0:
        return
        
    volatility_ratio = current_atr / historical_atr_baseline
    if volatility_ratio > MAX_VOLATILITY_ATR:
        logger.error(
            f"[CIRCUIT BREAKER] EXTREME VOLATILITY DETECTED (Ratio: {volatility_ratio:.2f}). "
            f"DISABLING TRADING!"
        )
        async with async_session_factory() as db:
            await set_trading_state("DISABLED", "system:breaker:volatility", f"Volatility ratio {volatility_ratio:.2f} > {MAX_VOLATILITY_ATR}", db_session=db)

# Start a background task for periodic checks (can be imported in main/worker loops)
async def circuit_breaker_loop():
    while True:
        await evaluate_daily_loss_breaker()
        await evaluate_api_failure_breaker()
        await asyncio.sleep(60) # Run checks every minute
