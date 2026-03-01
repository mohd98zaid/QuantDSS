"""
MultiTimeframeFilter — Higher-timeframe trend confirmation.
Only allow signals that align with the higher timeframe trend direction.
E.g., only take 5-min BUY signals when the daily trend is UP (price > EMA(50) on daily).
"""
import pandas as pd

from app.core.logging import logger
from app.engine.indicators import IndicatorEngine


class MultiTimeframeFilter:
    """
    Filters signals based on higher-timeframe trend alignment.

    Usage:
        mtf = MultiTimeframeFilter(htf_ema_period=50)
        if mtf.is_aligned(daily_candles, signal_type="BUY"):
            # proceed with signal
    """

    def __init__(self, htf_ema_period: int = 50):
        self.htf_ema_period = htf_ema_period

    def compute_trend(self, htf_candles: pd.DataFrame) -> str:
        """
        Determine the higher-timeframe trend.

        Args:
            htf_candles: Daily (or higher TF) OHLCV DataFrame

        Returns:
            'UP', 'DOWN', or 'NEUTRAL'
        """
        if htf_candles is None or len(htf_candles) < self.htf_ema_period + 5:
            logger.debug("MTF: Insufficient HTF data — returning NEUTRAL")
            return "NEUTRAL"

        ema = IndicatorEngine.ema(htf_candles["close"], self.htf_ema_period)

        if ema.isna().iloc[-1]:
            return "NEUTRAL"

        current_close = float(htf_candles["close"].iloc[-1])
        current_ema = float(ema.iloc[-1])
        prev_close = float(htf_candles["close"].iloc[-2])
        prev_ema = float(ema.iloc[-2])

        # Strong uptrend: price above EMA and slope is positive
        ema_slope = current_ema - prev_ema

        if current_close > current_ema and ema_slope > 0:
            return "UP"
        elif current_close < current_ema and ema_slope < 0:
            return "DOWN"
        else:
            return "NEUTRAL"

    def is_aligned(self, htf_candles: pd.DataFrame, signal_type: str) -> bool:
        """
        Check if a signal aligns with the higher-timeframe trend.

        Args:
            htf_candles: Daily OHLCV DataFrame
            signal_type: 'BUY' or 'SELL'

        Returns:
            True if signal direction matches HTF trend (or trend is NEUTRAL)
        """
        trend = self.compute_trend(htf_candles)

        if trend == "NEUTRAL":
            return True  # Allow all signals when trend is unclear

        if signal_type == "BUY" and trend == "UP":
            logger.info(f"MTF ✓ BUY aligned with daily UP trend")
            return True
        elif signal_type == "SELL" and trend == "DOWN":
            logger.info(f"MTF ✓ SELL aligned with daily DOWN trend")
            return True
        else:
            logger.info(f"MTF ✗ {signal_type} rejected — daily trend is {trend}")
            return False

    def get_trend_summary(self, htf_candles: pd.DataFrame) -> dict:
        """Return trend summary for dashboard display."""
        if htf_candles is None or len(htf_candles) < self.htf_ema_period + 5:
            return {"trend": "NEUTRAL", "ema_value": None, "close": None}

        ema = IndicatorEngine.ema(htf_candles["close"], self.htf_ema_period)
        trend = self.compute_trend(htf_candles)

        return {
            "trend": trend,
            "ema_period": self.htf_ema_period,
            "ema_value": round(float(ema.iloc[-1]), 2) if not ema.isna().iloc[-1] else None,
            "close": round(float(htf_candles["close"].iloc[-1]), 2),
        }
