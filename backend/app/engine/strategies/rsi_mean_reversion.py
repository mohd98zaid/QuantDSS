"""
RSI Mean Reversion Strategy — Pullback entries in established trends.
Enter oversold bounces in uptrend; enter overbought rejections in downtrend.
"""

import pandas as pd

from app.core.logging import logger
from app.engine.base_strategy import BaseStrategy, RawSignal
from app.engine.indicators import IndicatorEngine


class RSIMeanReversionStrategy(BaseStrategy):
    """
    RSI Mean Reversion — Pullback in Trend Strategy.

    LONG Entry (Oversold Bounce):
      1. Price above EMA(50) — confirms uptrend
      2. RSI was below oversold (35) and has just crossed above — recovery signal

    SHORT Entry (Overbought Rejection):
      1. Price below EMA(50) — confirms downtrend
      2. RSI was above overbought (65) and has just crossed below
    """

    @property
    def strategy_type(self) -> str:
        return "rsi_mean_reversion"

    @property
    def min_candles_required(self) -> int:
        return max(
            self.params.get("ema_trend", 50),
            self.params.get("rsi_period", 14),
            self.params.get("atr_period", 14),
        ) + 5

    def evaluate(self, candles: pd.DataFrame, symbol_id: int) -> RawSignal | None:
        """Evaluate RSI mean reversion conditions on latest candles."""
        if len(candles) < self.min_candles_required:
            return None

        # Compute indicators
        df = IndicatorEngine.compute_strategy_indicators(
            candles, self.strategy_type, self.params
        )

        # Validate indicators exist
        if df["rsi"].isna().iloc[-1] or df["ema_trend"].isna().iloc[-1]:
            return None
        if df["atr"].isna().iloc[-1]:
            return None

        # Extract values
        rsi_prev = float(df["rsi"].iloc[-2])
        rsi_curr = float(df["rsi"].iloc[-1])
        ema_trend_curr = float(df["ema_trend"].iloc[-1])
        close_curr = float(df["close"].iloc[-1])
        atr_curr = float(df["atr"].iloc[-1])
        candle_time = df.index[-1] if isinstance(df.index, pd.DatetimeIndex) else df["time"].iloc[-1]

        rsi_oversold = self.params.get("rsi_oversold", 35)
        rsi_overbought = self.params.get("rsi_overbought", 65)
        atr_sl = self.params.get("atr_multiplier_sl", 1.0)
        rr_ratio = self.params.get("risk_reward", 2.0)

        # ─── Check LONG (Oversold Bounce) ─────────────────────
        if (
            close_curr > ema_trend_curr              # In uptrend
            and rsi_prev < rsi_oversold               # Was oversold
            and rsi_curr > rsi_oversold               # Now recovering (crossed above threshold)
        ):
            entry = close_curr
            stop_loss = entry - (atr_sl * atr_curr)
            sl_distance = entry - stop_loss
            target = entry + (sl_distance * rr_ratio)

            logger.info(
                f"RSI MR BUY signal: {symbol_id} @ {entry:.2f} "
                f"RSI={rsi_curr:.1f} SL={stop_loss:.2f} T={target:.2f}"
            )

            return RawSignal(
                symbol_id=symbol_id,
                strategy_id=self.strategy_id,
                signal_type="BUY",
                entry_price=entry,
                stop_loss=stop_loss,
                target_price=target,
                atr_value=atr_curr,
                candle_time=candle_time,
            )

        # ─── Check SHORT (Overbought Rejection) ──────────────
        if (
            close_curr < ema_trend_curr              # In downtrend
            and rsi_prev > rsi_overbought             # Was overbought
            and rsi_curr < rsi_overbought             # Now falling (crossed below threshold)
        ):
            entry = close_curr
            stop_loss = entry + (atr_sl * atr_curr)
            sl_distance = stop_loss - entry
            target = entry - (sl_distance * rr_ratio)

            logger.info(
                f"RSI MR SELL signal: {symbol_id} @ {entry:.2f} "
                f"RSI={rsi_curr:.1f} SL={stop_loss:.2f} T={target:.2f}"
            )

            return RawSignal(
                symbol_id=symbol_id,
                strategy_id=self.strategy_id,
                signal_type="SELL",
                entry_price=entry,
                stop_loss=stop_loss,
                target_price=target,
                atr_value=atr_curr,
                candle_time=candle_time,
            )

        return None
