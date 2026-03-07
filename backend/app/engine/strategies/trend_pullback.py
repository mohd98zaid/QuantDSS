"""
TrendPullbackStrategy — EMA21 pullback in an established trend.

Architecture doc: Section 6.3 — Trend Pullback.

Concept:
  In an established trend, price pulls back to EMA21 and then resumes.
  The resumption candle confirmed by RSI returning to neutral (40-60)
  is the entry signal.

This is distinct from TrendContinuationStrategy (§ — that one uses
VWAP as the pullback magnet. This one uses EMA21, as specified in §6.3.

Entry Conditions (LONG):
  1. EMA9 > EMA21 > EMA50 — full bullish alignment (uptrend)
  2. Previous bar's low touched or pierced EMA21 (pullback happened)
  3. Current bar closes back above EMA21 (resumption confirmed)
  4. RSI between 40–60 (neutral — not overextended)

Entry Conditions (SHORT — inverse):
  1. EMA9 < EMA21 < EMA50 — full bearish alignment
  2. Previous bar's high touched or pierced EMA21
  3. Current bar closes back below EMA21
  4. RSI between 40–60

Stop Loss:
  EMA21 - 1×ATR (LONG) / EMA21 + 1×ATR (SHORT)

Target:
  Previous swing high/proxy = 3×ATR from entry

Best conditions:
  Clearly trending days, mid-morning pullbacks 09:45–11:00 IST.
"""

import pandas as pd

from app.core.logging import logger
from app.engine.base_strategy import BaseStrategy, RawSignal
from app.engine.indicators import IndicatorEngine


class TrendPullbackStrategy(BaseStrategy):
    """
    Trend Pullback — EMA21 pullback entry with RSI neutral confirmation.
    """

    @property
    def strategy_type(self) -> str:
        return "trend_pullback"

    @property
    def min_candles_required(self) -> int:
        return max(
            self.params.get("ema_slow", 50),
            self.params.get("rsi_period", 14),
            self.params.get("atr_period", 14),
        ) + 10

    def evaluate(self, candles: pd.DataFrame, symbol_id: int) -> RawSignal | None:
        """Evaluate EMA21 pullback conditions."""
        if len(candles) < self.min_candles_required:
            return None

        # Compute indicators if not pre-computed
        if "ema_21" not in candles.columns:
            df = IndicatorEngine.compute_strategy_indicators(
                candles, self.strategy_type, self.params
            )
        else:
            df = candles

        for col in ("ema_9", "ema_21", "ema_50", "rsi", "atr"):
            if col not in df.columns or pd.isna(df[col].iloc[-1]):
                return None

        curr = df.iloc[-1]
        prev = df.iloc[-2]

        ema9  = float(curr["ema_9"])
        ema21 = float(curr["ema_21"])
        ema50 = float(curr["ema_50"])
        rsi   = float(curr["rsi"])
        atr   = float(curr["atr"])

        close_curr = float(curr["close"])
        low_prev   = float(prev["low"])
        high_prev  = float(prev["high"])

        ema21_prev = float(prev["ema_21"]) if not pd.isna(prev["ema_21"]) else ema21

        rsi_low  = self.params.get("rsi_low",  40)
        rsi_high = self.params.get("rsi_high", 60)
        atr_sl   = self.params.get("atr_multiplier_sl", 1.0)
        atr_tgt  = self.params.get("atr_multiplier_target", 3.0)

        candle_time = (
            df.index[-1] if isinstance(df.index, pd.DatetimeIndex)
            else df["time"].iloc[-1]
        )

        # RSI neutral zone check
        rsi_neutral = rsi_low <= rsi <= rsi_high

        # ── LONG: pulled back to EMA21, now resuming up ───────────────────
        if (
            ema9 > ema21 > ema50              # Full bullish EMA stack
            and low_prev <= ema21_prev        # Previous bar touched/pierced EMA21
            and close_curr > ema21            # Current bar resumed above EMA21
            and rsi_neutral                   # RSI in neutral zone (not overextended)
        ):
            entry     = close_curr
            stop_loss = ema21 - atr_sl * atr
            sl_dist   = entry - stop_loss
            if sl_dist <= 0:
                return None
            target = entry + atr_tgt * atr

            # Confidence: deeper the EMA stack + tighter RSI midpoint = better
            ema_gap_score  = min(20.0, abs(ema9 - ema50) / ema50 * 1000)
            rsi_score      = 15.0 - abs(rsi - 50) * 0.5   # best at RSI=50
            bounce_depth   = (ema21_prev - low_prev) / atr * 10
            score = 45.0 + ema_gap_score + max(0.0, rsi_score) + min(15.0, bounce_depth)
            score = min(100.0, max(0.0, score))

            logger.info(
                f"TrendPullback BUY: symbol={symbol_id} @ {entry:.2f} "
                f"EMA21={ema21:.2f} RSI={rsi:.1f} SL={stop_loss:.2f} T={target:.2f} conf={score:.0f}"
            )
            return RawSignal(
                symbol_id=symbol_id,
                strategy_id=self.strategy_id,
                signal_type="BUY",
                entry_price=entry,
                stop_loss=stop_loss,
                target_price=target,
                atr_value=atr,
                candle_time=candle_time,
                confidence_score=score,
            )

        # ── SHORT: pulled back to EMA21, now resuming down ───────────────
        if (
            ema9 < ema21 < ema50              # Full bearish EMA stack
            and high_prev >= ema21_prev       # Previous bar touched/pierced EMA21 from below
            and close_curr < ema21            # Current bar resumed below EMA21
            and rsi_neutral
        ):
            entry     = close_curr
            stop_loss = ema21 + atr_sl * atr
            sl_dist   = stop_loss - entry
            if sl_dist <= 0:
                return None
            target = entry - atr_tgt * atr

            ema_gap_score  = min(20.0, abs(ema9 - ema50) / ema50 * 1000)
            rsi_score      = 15.0 - abs(rsi - 50) * 0.5
            bounce_depth   = (high_prev - ema21_prev) / atr * 10
            score = 45.0 + ema_gap_score + max(0.0, rsi_score) + min(15.0, bounce_depth)
            score = min(100.0, max(0.0, score))

            logger.info(
                f"TrendPullback SELL: symbol={symbol_id} @ {entry:.2f} "
                f"EMA21={ema21:.2f} RSI={rsi:.1f} SL={stop_loss:.2f} T={target:.2f} conf={score:.0f}"
            )
            return RawSignal(
                symbol_id=symbol_id,
                strategy_id=self.strategy_id,
                signal_type="SELL",
                entry_price=entry,
                stop_loss=stop_loss,
                target_price=target,
                atr_value=atr,
                candle_time=candle_time,
                confidence_score=score,
            )

        return None
