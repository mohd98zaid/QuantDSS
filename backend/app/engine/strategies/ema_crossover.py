"""
EMA Crossover Strategy — Trend-following.
Enter when fast EMA crosses above slow EMA with price above both and volume confirmation.
"""

import pandas as pd

from app.core.logging import logger
from app.engine.base_strategy import BaseStrategy, CandidateSignal
from app.engine.indicators import IndicatorEngine


class EMACrossoverStrategy(BaseStrategy):
    """
    EMA Crossover — Trend Following Strategy.

    LONG Entry:
      1. EMA fast crosses above EMA slow (golden cross on closed candle)
      2. Price above both EMAs (trend confirmation)
      3. Volume above 20-period average (breakout confirmation)

    SHORT Entry (reverse logic):
      1. EMA fast crosses below EMA slow (death cross)
      2. Price below both EMAs
      3. Volume above average
    """

    @property
    def strategy_type(self) -> str:
        return "ema_crossover"

    @property
    def min_candles_required(self) -> int:
        return max(
            self.params.get("ema_slow", 21),
            self.params.get("volume_ma_period", 20),
            self.params.get("atr_period", 14),
        ) + 5  # Buffer

    def evaluate(self, candles: pd.DataFrame, symbol_id: int) -> CandidateSignal | None:
        """Evaluate EMA crossover conditions on latest candles."""
        if len(candles) < self.min_candles_required:
            return None

        # Compute indicators only if they don't exist (e.g. not precomputed by BacktestEngine)
        if "ema_fast" not in candles.columns:
            df = IndicatorEngine.compute_strategy_indicators(
                candles, self.strategy_type, self.params
            )
        else:
            df = candles

        # Need at least 2 rows with valid indicators
        if df["ema_fast"].isna().iloc[-1] or df["ema_slow"].isna().iloc[-1]:
            return None
        if df["atr"].isna().iloc[-1]:
            return None

        # Current bar (last closed candle) = [-1], previous bar = [-2]
        ema_fast_prev = df["ema_fast"].iloc[-2]
        ema_slow_prev = df["ema_slow"].iloc[-2]
        ema_fast_curr = df["ema_fast"].iloc[-1]
        ema_slow_curr = df["ema_slow"].iloc[-1]
        close_curr = float(df["close"].iloc[-1])
        volume_curr = float(df["volume"].iloc[-1])
        volume_ma_curr = float(df["volume_ma"].iloc[-1]) if not pd.isna(df["volume_ma"].iloc[-1]) else 0
        atr_curr = float(df["atr"].iloc[-1])
        candle_time = df.index[-1] if isinstance(df.index, pd.DatetimeIndex) else df["time"].iloc[-1]

        atr_sl = self.params.get("atr_multiplier_sl", 1.5)
        atr_target = self.params.get("atr_multiplier_target", 3.0)

        # ─── Check LONG (BUY) conditions ─────────────────────
        if (
            ema_fast_prev < ema_slow_prev       # Was below (no cross yet)
            and ema_fast_curr > ema_slow_curr    # Now above (golden cross!)
            and close_curr > ema_fast_curr       # Price above both EMAs
            and close_curr > ema_slow_curr
            and volume_curr > volume_ma_curr     # Above-average volume
        ):
            entry = close_curr
            stop_loss = entry - (atr_sl * atr_curr)
            target = entry + (atr_target * atr_curr)

            # Confidence score (0–100)
            score = 50.0  # Base: required conditions met
            if volume_ma_curr > 0:
                rvol = volume_curr / volume_ma_curr
                if rvol >= 2.0:
                    score += 15.0
                elif rvol >= 1.5:
                    score += 8.0
            ema_gap_pct = (ema_fast_curr - ema_slow_curr) / ema_slow_curr if ema_slow_curr > 0 else 0
            score += min(15.0, ema_gap_pct * 500)  # up to +15 for strong gap
            ema_slope = float(ema_fast_curr) - float(df["ema_fast"].iloc[-3]) if len(df) >= 3 else 0.0
            if ema_slope > 0:
                score += 10.0
            atr_prev = float(df["atr"].iloc[-2]) if not pd.isna(df["atr"].iloc[-2]) else atr_curr
            if atr_curr > atr_prev:
                score += 10.0  # ATR expanding
            score = min(100.0, max(0.0, score))

            logger.info(
                f"EMA Crossover BUY signal: {symbol_id} @ {entry:.2f} "
                f"SL={stop_loss:.2f} T={target:.2f} ATR={atr_curr:.4f} conf={score:.0f}"
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

        # ─── Check SHORT (SELL) conditions ────────────────────
        if (
            ema_fast_prev > ema_slow_prev       # Was above
            and ema_fast_curr < ema_slow_curr    # Now below (death cross!)
            and close_curr < ema_fast_curr       # Price below both EMAs
            and close_curr < ema_slow_curr
            and volume_curr > volume_ma_curr     # Above-average volume
        ):
            entry = close_curr
            stop_loss = entry + (atr_sl * atr_curr)
            target = entry - (atr_target * atr_curr)

            # Confidence score (mirrors BUY logic)
            score = 50.0
            if volume_ma_curr > 0:
                rvol = volume_curr / volume_ma_curr
                if rvol >= 2.0:
                    score += 15.0
                elif rvol >= 1.5:
                    score += 8.0
            ema_gap_pct = (ema_slow_curr - ema_fast_curr) / ema_slow_curr if ema_slow_curr > 0 else 0
            score += min(15.0, ema_gap_pct * 500)
            ema_slope = float(ema_fast_curr) - float(df["ema_fast"].iloc[-3]) if len(df) >= 3 else 0.0
            if ema_slope < 0:
                score += 10.0
            atr_prev = float(df["atr"].iloc[-2]) if not pd.isna(df["atr"].iloc[-2]) else atr_curr
            if atr_curr > atr_prev:
                score += 10.0
            score = min(100.0, max(0.0, score))

            logger.info(
                f"EMA Crossover SELL signal: {symbol_id} @ {entry:.2f} "
                f"SL={stop_loss:.2f} T={target:.2f} ATR={atr_curr:.4f} conf={score:.0f}"
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
