"""
Regime Scheduler — Auto-detects market regime and writes it to RiskConfig.

Fix (Audit Category 7):
  The RegimeDetector existed but was never scheduled to auto-run.
  config.market_regime was always stale. This module provides the
  scheduled job that refreshes it every 5 minutes during market hours.

  Uses Nifty 50 (NSE_INDEX|Nifty 50) candles for market-wide context
  rather than individual stock candles.
"""
from datetime import datetime, timezone, timedelta

from app.core.database import async_session_factory
from app.core.logging import logger
from app.engine.regime_detector import RegimeDetector
from app.ingestion.upstox_http import UpstoxHTTPClient

IST = timezone(timedelta(hours=5, minutes=30))
NIFTY_INSTRUMENT_KEY = "NSE_INDEX|Nifty 50"


def _is_market_hours() -> bool:
    now = datetime.now(IST)
    if now.weekday() >= 5:
        return False
    h, m = now.hour, now.minute
    return (h > 9 or (h == 9 and m >= 15)) and (h < 15 or (h == 15 and m <= 30))


async def update_regime_in_db() -> None:
    """
    Scheduled task: detect the current intraday regime from Nifty 50 candles
    and persist it to RiskConfig.market_regime.

    Runs every 5 minutes during market hours via APScheduler.
    Falls back to "NONE" if data cannot be fetched.
    """
    if not _is_market_hours():
        return

    try:
        from sqlalchemy import select
        import pandas as pd

        upstox = UpstoxHTTPClient()

        # Use Nifty 50 intraday 1-min candles for market-wide regime context
        candles_raw = await upstox.get_intraday_candles(NIFTY_INSTRUMENT_KEY, "1min")

        if not candles_raw or len(candles_raw) < 40:
            logger.debug(
                f"regime_scheduler: insufficient Nifty candles ({len(candles_raw)}) — skipping"
            )
            return

        df = pd.DataFrame(candles_raw)
        df["time"] = pd.to_datetime(df["time"])
        df = df.sort_values("time").reset_index(drop=True)

        detector = RegimeDetector()
        regime = detector.detect(df)

        # Write to DB
        from app.models.risk_config import RiskConfig
        async with async_session_factory() as db:
            result = await db.execute(select(RiskConfig).limit(1))
            config = result.scalar_one_or_none()
            if config:
                config.market_regime = regime
                await db.commit()
                logger.info(f"RegimeScheduler: Nifty 50 regime updated → {regime}")
            else:
                logger.warning("RegimeScheduler: No RiskConfig found in DB")

    except Exception as e:
        logger.error(f"RegimeScheduler error: {e}")
