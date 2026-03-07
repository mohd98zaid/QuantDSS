"""
FailedBreakoutStrategy — Fade false breakouts above the Opening Range.

Architecture doc: Section 6.6 — Failed Breakout (Fade).

Concept:
  Price breaks above OR_High (or below OR_Low) but closes back inside the
  opening range within 1-2 candles, indicating the crowd was caught on the
  wrong side. We fade the breakout in the opposite direction.

Entry conditions (SHORT / SELL):
  1. Price broke above OR_High by > 0.3% in the previous bar (bar[-2])
  2. Current bar (bar[-1]) closes back below OR_High
  3. RSI > 68 at breakout (overbought — crowd piling in)
  4. Reversal candle volume ≥ breakout candle volume (real selling)

Entry conditions (LONG / BUY — failed breakdown):
  1. Price broke below OR_Low by > 0.3% in bar[-2]
  2. Current bar closes back above OR_Low
  3. RSI < 32 at breakout (oversold)
  4. Recovery candle volume ≥ breakdown candle volume

Stop loss:
  SELL: high of the failed breakout candle + 0.3%
  BUY:  low of the failed breakdown candle - 0.3%

Target:
  VWAP (if available) or OR_High - 1×ATR (SELL) / OR_Low + 1×ATR (BUY)

Best conditions:
  RANGE or HIGH_VOLATILITY days, morning session (09:20–11:00 IST)
"""

import pandas as pd

from app.core.logging import logger
from app.engine.base_strategy import BaseStrategy, RawSignal
from app.engine.indicators import IndicatorEngine


class FailedBreakoutStrategy(BaseStrategy):
    """
    Failed Breakout (Fade) — contrarian strategy that shorts failed
    ORB breakouts and buys failed ORB breakdowns.
    """

    @property
    def strategy_type(self) -> str:
        return "failed_breakout"

    @property
    def min_candles_required(self) -> int:
        # Needs: 15-min OR window (15 bars) + enough for ATR + RSI
        return max(
            self.params.get("atr_period", 14),
            self.params.get("rsi_period", 14),
        ) + 20

    def evaluate(self, candles: pd.DataFrame, symbol_id: int) -> RawSignal | None:
        """Evaluate failed breakout conditions on the last two closed candles."""
        if len(candles) < self.min_candles_required:
            return None

        # Compute indicators if not already present
        if "atr" not in candles.columns:
            df = IndicatorEngine.compute_strategy_indicators(
                candles, self.strategy_type, self.params
            )
        else:
            df = candles

        # Guard: need valid indicator values
        if df["atr"].isna().iloc[-1] or df["rsi"].isna().iloc[-1]:
            return None
        if "or_high" not in df.columns or "or_low" not in df.columns:
            return None

        or_high = df["or_high"].iloc[-1]
        or_low  = df["or_low"].iloc[-1]

        if pd.isna(or_high) or pd.isna(or_low) or float(or_high) <= 0:
            return None

        or_high = float(or_high)
        or_low  = float(or_low)

        # Current bar and previous bar
        curr       = df.iloc[-1]
        prev       = df.iloc[-2]   # The bar that broke the OR level

        atr_curr   = float(curr["atr"])
        rsi_at_bo  = float(prev["rsi"]) if not pd.isna(prev["rsi"]) else 50.0

        close_prev = float(prev["close"])
        high_prev  = float(prev["high"])
        low_prev   = float(prev["low"])
        vol_prev   = float(prev["volume"])

        close_curr = float(curr["close"])
        vol_curr   = float(curr["volume"])

        vwap_curr  = float(curr["vwap"]) if "vwap" in df.columns and not pd.isna(curr.get("vwap")) else None

        # Breakout threshold (0.3% above OR)
        break_thresh = self.params.get("breakout_threshold_pct", 0.003)

        candle_time = df.index[-1] if isinstance(df.index, pd.DatetimeIndex) else df["time"].iloc[-1]

        # ── SELL: failed breakout above OR_High ──────────────
        if (
            close_prev > or_high * (1 + break_thresh)   # prev bar broke above
            and close_curr < or_high                      # curr bar returned below OR_High
            and rsi_at_bo > self.params.get("rsi_overbought", 68)   # overbought at breakout
            and vol_curr >= vol_prev * self.params.get("reversal_vol_multiplier", 0.9)  # volume confirms
        ):
            entry     = close_curr
            # SL: high of the failed breakout candle + buffer
            stop_loss = high_prev * (1 + self.params.get("sl_buffer_pct", 0.003))
            # Target: VWAP if available, else OR_High - 1×ATR
            if vwap_curr and vwap_curr < or_high:
                target = max(vwap_curr, or_high - atr_curr)
            else:
                target = or_high - atr_curr

            if target >= entry or stop_loss <= entry:
                return None   # Invalid geometry

            logger.info(
                f"FailedBreakout SELL signal: symbol={symbol_id} @ {entry:.2f} "
                f"SL={stop_loss:.2f} T={target:.2f} RSI_at_BO={rsi_at_bo:.1f}"
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
                confidence_score=70.0,  # Base — SignalScorer will refine
            )

        # ── BUY: failed breakdown below OR_Low ───────────────
        if (
            close_prev < or_low * (1 - break_thresh)    # prev bar broke below
            and close_curr > or_low                       # curr bar recovered above OR_Low
            and rsi_at_bo < self.params.get("rsi_oversold", 32)    # oversold at breakdown
            and vol_curr >= vol_prev * self.params.get("reversal_vol_multiplier", 0.9)
        ):
            entry     = close_curr
            stop_loss = low_prev * (1 - self.params.get("sl_buffer_pct", 0.003))
            if vwap_curr and vwap_curr > or_low:
                target = min(vwap_curr, or_low + atr_curr)
            else:
                target = or_low + atr_curr

            if target <= entry or stop_loss >= entry:
                return None

            logger.info(
                f"FailedBreakout BUY signal: symbol={symbol_id} @ {entry:.2f} "
                f"SL={stop_loss:.2f} T={target:.2f} RSI_at_BO={rsi_at_bo:.1f}"
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
                confidence_score=70.0,
            )

        return None
