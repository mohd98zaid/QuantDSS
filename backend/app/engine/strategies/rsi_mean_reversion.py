"""
RSI Mean Reversion Strategy — Pullback entries in established trends.
Enter oversold bounces in uptrend; enter overbought rejections in downtrend.
"""

import pandas as pd

from app.core.logging import logger
from app.engine.base_strategy import BaseStrategy, CandidateSignal
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

    def evaluate(self, candles: pd.DataFrame, symbol_id: int) -> CandidateSignal | None:
        """Evaluate RSI mean reversion conditions on latest candles."""
        if len(candles) < self.min_candles_required:
            return None

        # Compute indicators only if they don't exist
        if "rsi" not in candles.columns:
            df = IndicatorEngine.compute_strategy_indicators(
                candles, self.strategy_type, self.params
            )
        else:
            df = candles

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

            # Confidence score: depth of RSI oversold + trend distance
            rsi_depth = max(0.0, rsi_oversold - rsi_prev)  # how far below oversold threshold
            trend_dist = (close_curr - ema_trend_curr) / ema_trend_curr if ema_trend_curr > 0 else 0
            score = 40.0 + min(30.0, rsi_depth * 2.0) + min(20.0, trend_dist * 500) + (10.0 if rsi_curr < rsi_oversold else 0.0)
            score = min(100.0, max(0.0, score))

            logger.info(
                f"RSI MR BUY signal: {symbol_id} @ {entry:.2f} "
                f"RSI={rsi_curr:.1f} SL={stop_loss:.2f} T={target:.2f} conf={score:.0f}"
            )

            return CandidateSignal(
                symbol_id=symbol_id,
                strategy_id=self.strategy_id,
                strategy_name=self.strategy_type,
                signal_type="BUY",
                entry_price=entry,
                stop_loss=stop_loss,
                target_price=target,
                atr_value=atr_curr,
                candle_time=candle_time,
                confidence_score=score,
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

            # Confidence score: depth of RSI overbought + downtrend distance
            rsi_depth = max(0.0, rsi_prev - rsi_overbought)
            trend_dist = (ema_trend_curr - close_curr) / ema_trend_curr if ema_trend_curr > 0 else 0
            score = 40.0 + min(30.0, rsi_depth * 2.0) + min(20.0, trend_dist * 500) + (10.0 if rsi_curr > rsi_overbought else 0.0)
            score = min(100.0, max(0.0, score))

            logger.info(
                f"RSI MR SELL signal: {symbol_id} @ {entry:.2f} "
                f"RSI={rsi_curr:.1f} SL={stop_loss:.2f} T={target:.2f} conf={score:.0f}"
            )

            return CandidateSignal(
                symbol_id=symbol_id,
                strategy_id=self.strategy_id,
                strategy_name=self.strategy_type,
                signal_type="SELL",
                entry_price=entry,
                stop_loss=stop_loss,
                target_price=target,
                atr_value=atr_curr,
                candle_time=candle_time,
                confidence_score=score,
            )

        return None
