"""
Trend Continuation Strategy — Pull-back entries in aligned trending markets.

Entry conditions:
  LONG:  EMA9 > EMA21 > EMA50 (full bullish alignment)
         + price pulls back to touch VWAP (within 0.2% ATR)
         + current candle closes bullish (close > open)

  SHORT: EMA9 < EMA21 < EMA50 (full bearish alignment)
         + price pulls back to touch VWAP
         + current candle closes bearish (close < open)

SL: Beyond VWAP (BUY: below VWAP; SELL: above VWAP)
Target: 1:2 R:R
"""
import pandas as pd

from app.core.logging import logger
from app.engine.base_strategy import BaseStrategy, CandidateSignal
from app.engine.indicators import IndicatorEngine


class TrendContinuationStrategy(BaseStrategy):
    """
    Trend Continuation — multi-EMA alignment with VWAP pullback.

    LONG conditions:
      1. EMA9 > EMA21 > EMA50 (all three aligned bullish)
      2. Price pulls back to VWAP (within tolerance)
      3. Candle closes bullish
    SHORT: Mirror inverse logic.
    """

    @property
    def strategy_type(self) -> str:
        return "trend_continuation"

    @property
    def min_candles_required(self) -> int:
        return max(
            self.params.get("ema_slow", 50),
            self.params.get("atr_period", 14),
        ) + 10

    def evaluate(self, candles: pd.DataFrame, symbol_id: int) -> CandidateSignal | None:
        """Evaluate trend continuation conditions."""
        if len(candles) < self.min_candles_required:
            return None

        if "ema_50" not in candles.columns or "vwap" not in candles.columns:
            df = IndicatorEngine.compute_strategy_indicators(
                candles, self.strategy_type, self.params
            )
        else:
            df = candles

        if df["ema_50"].isna().iloc[-1] or df["vwap"].isna().iloc[-1]:
            return None
        if df["atr"].isna().iloc[-1]:
            return None

        # Params
        vwap_tol  = self.params.get("vwap_tolerance_atr", 0.3)   # close to VWAP = within 0.3 ATR
        atr_sl    = self.params.get("atr_multiplier_sl", 1.0)
        rr_ratio  = self.params.get("risk_reward", 2.0)

        curr  = df.iloc[-1]
        ema9  = float(curr["ema_9"])  if "ema_9"  in df.columns else float(IndicatorEngine.ema(df["close"], 9).iloc[-1])
        ema21 = float(curr["ema_21"]) if "ema_21" in df.columns else float(IndicatorEngine.ema(df["close"], 21).iloc[-1])
        ema50 = float(curr["ema_50"])
        vwap  = float(curr["vwap"])
        close = float(curr["close"])
        open_ = float(curr["open"])
        atr   = float(curr["atr"])
        candle_time = df.index[-1] if isinstance(df.index, pd.DatetimeIndex) else df["time"].iloc[-1]

        # Distance from VWAP in ATR units
        vwap_dist = abs(close - vwap) / atr if atr > 0 else 999.0

        is_pullback_to_vwap = vwap_dist <= vwap_tol

        # Triple alignment gap → confidence boost
        def triple_gap_score(e9: float, e21: float, e50: float) -> float:
            gap21 = abs(e9 - e21) / e21 * 100 if e21 > 0 else 0
            gap50 = abs(e21 - e50) / e50 * 100 if e50 > 0 else 0
            return min(30.0, (gap21 + gap50) * 3.0)

        # ─── LONG ─────────────────────────────────────────────
        if (
            ema9 > ema21 > ema50          # Full bullish alignment
            and is_pullback_to_vwap       # Pulled back to VWAP
            and close > open_             # Bullish candle body
        ):
            entry     = close
            stop_loss = vwap - (atr_sl * atr)
            sl_dist   = entry - stop_loss
            if sl_dist <= 0:
                return None
            target = entry + sl_dist * rr_ratio

            score = 45.0 + triple_gap_score(ema9, ema21, ema50)
            score += 15.0 if vwap_dist < 0.15 else 5.0   # tighter pullback = higher confidence
            score = min(100.0, max(0.0, score))

            logger.info(
                f"TrendContinuation BUY: {symbol_id} @ {entry:.2f} "
                f"VWAP={vwap:.2f} SL={stop_loss:.2f} T={target:.2f} conf={score:.0f}"
            )
            return CandidateSignal(
                symbol_id=symbol_id,
                strategy_id=self.strategy_id,
                strategy_name=self.strategy_type,
                signal_type="BUY",
                entry_price=entry,
                stop_loss=stop_loss,
                target_price=target,
                atr_value=atr,
                candle_time=candle_time,
                confidence_score=score,
            )

        # ─── SHORT ────────────────────────────────────────────
        if (
            ema9 < ema21 < ema50          # Full bearish alignment
            and is_pullback_to_vwap
            and close < open_             # Bearish candle body
        ):
            entry     = close
            stop_loss = vwap + (atr_sl * atr)
            sl_dist   = stop_loss - entry
            if sl_dist <= 0:
                return None
            target = entry - sl_dist * rr_ratio

            score = 45.0 + triple_gap_score(ema9, ema21, ema50)
            score += 15.0 if vwap_dist < 0.15 else 5.0
            score = min(100.0, max(0.0, score))

            logger.info(
                f"TrendContinuation SELL: {symbol_id} @ {entry:.2f} "
                f"VWAP={vwap:.2f} SL={stop_loss:.2f} T={target:.2f} conf={score:.0f}"
            )
            return CandidateSignal(
                symbol_id=symbol_id,
                strategy_id=self.strategy_id,
                strategy_name=self.strategy_type,
                signal_type="SELL",
                entry_price=entry,
                stop_loss=stop_loss,
                target_price=target,
                atr_value=atr,
                candle_time=candle_time,
                confidence_score=score,
            )

        return None
