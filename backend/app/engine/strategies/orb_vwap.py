"""
ORBVWAPStrategy — Opening Range Breakout + VWAP Confirmation.

Strategy Rules (as defined in the problem statement):
  Opening Range: First 15 minutes of trading (9:15–9:30 AM IST)
    - orb_high: highest price during the opening range
    - orb_low:  lowest price during the opening range

  LONG Entry (BUY) — ALL conditions must be true:
    1. Price breaks ABOVE orb_high
    2. Price > VWAP (above intraday average price)
    3. EMA9 > EMA21 (short-term trend is up)
    4. Volume > 10-period volume average (volume spike)

  SHORT Entry (SELL) — ALL conditions must be true:
    1. Price breaks BELOW orb_low
    2. Price < VWAP (below intraday average price)
    3. EMA9 < EMA21 (short-term trend is down)
    4. Volume > 10-period volume average (volume spike)

  Stop Loss:
    - BUY : entry - (atr_sl × ATR) OR orb_low (whichever is tighter)
    - SELL: entry + (atr_sl × ATR) OR orb_high (whichever is tighter)

  Target:
    - Risk:Reward = 1:2 minimum (configurable via risk_reward param)

  Exit Rules:
    - SL hit, Target hit, or EOD at 3:15 PM (handled by paper_monitor.py)
"""

from datetime import timezone, timedelta
from typing import cast

import pandas as pd

from app.core.logging import logger
from app.engine.base_strategy import BaseStrategy, CandidateSignal
from app.engine.indicators import IndicatorEngine

# IST = UTC+5:30
IST = timezone(timedelta(hours=5, minutes=30))

# Opening range window (inclusive on both ends)
ORB_START_HOUR, ORB_START_MIN = 9, 15
ORB_END_HOUR,   ORB_END_MIN   = 9, 30


def _to_ist(ts: pd.Timestamp) -> pd.Timestamp:
    """Convert a timezone-aware Timestamp to IST."""
    if ts.tzinfo is None:
        return ts
    return ts.tz_convert(IST)


class ORBVWAPStrategy(BaseStrategy):
    """
    Opening Range Breakout confirmed with VWAP, EMA crossover, and volume.

    Works on intraday timeframes (1min, 5min, 15min).
    Requires a DatetimeIndex with timezone info to identify the opening range.
    Falls back to a whole-series high/low if no timezone data is present.
    """

    @property
    def strategy_type(self) -> str:
        return "orb_vwap"

    @property
    def min_candles_required(self) -> int:
        # Need at least enough for ORB + a few breakout candles
        return max(self.params.get("ema_slow", 21), self.params.get("volume_ma_period", 10)) + 5

    # ─────────────────────────────────────────────────────────────────────────

    def _get_orb_levels(self, df: pd.DataFrame) -> tuple[float | None, float | None]:
        """
        Identify the Opening Range High and Low from the first 15 minutes
        of the trading session (9:15–9:30 AM IST).

        Returns (orb_high, orb_low), or (None, None) if ORB cannot be determined.
        """
        if not isinstance(df.index, pd.DatetimeIndex):
            return None, None

        idx = df.index
        if idx.tz is not None:
            import pytz
            ist_zone = pytz.timezone("Asia/Kolkata")
            idx_ist = idx.tz_convert(ist_zone)
        else:
            idx_ist = idx

        # Filter candles within the opening range window
        orb_mask = (
            (idx_ist.hour == ORB_START_HOUR) & (idx_ist.minute >= ORB_START_MIN)
        ) | (
            (idx_ist.hour == ORB_END_HOUR) & (idx_ist.minute <= ORB_END_MIN)
        )
        # Handle both 9:15 and boundaries cleanly
        orb_mask = (
            ((idx_ist.hour == ORB_START_HOUR) & (idx_ist.minute >= ORB_START_MIN))
            | ((idx_ist.hour == ORB_END_HOUR) & (idx_ist.minute <= ORB_END_MIN) & (ORB_END_HOUR != ORB_START_HOUR))
        )
        # Simplified: any candle strictly between 9:15 and 9:30 AM IST
        in_orb = (
            (idx_ist.hour * 60 + idx_ist.minute) >= (ORB_START_HOUR * 60 + ORB_START_MIN)
        ) & (
            (idx_ist.hour * 60 + idx_ist.minute) <= (ORB_END_HOUR * 60 + ORB_END_MIN)
        )

        orb_df = df[in_orb]
        if orb_df.empty:
            # No ORB window data available — may be outside market hours or daily bars
            # Fall back to first N candles of today
            return None, None

        return float(orb_df["high"].max()), float(orb_df["low"].min())

    # ─────────────────────────────────────────────────────────────────────────

    def evaluate(self, candles: pd.DataFrame, symbol_id: int) -> CandidateSignal | None:
        """
        Evaluate ORB+VWAP breakout conditions on the latest candle.

        Returns a RawSignal if conditions are met, else None.
        """
        if len(candles) < self.min_candles_required:
            return None

        # Compute indicators if not already pre-computed by BacktestEngine
        if "vwap" not in candles.columns:
            df = IndicatorEngine.compute_strategy_indicators(
                candles, self.strategy_type, self.params
            )
        else:
            df = candles

        # Check required columns are ready
        for col in ("vwap", "ema_fast", "ema_slow", "atr", "volume_ma"):
            if col not in df.columns or df[col].isna().iloc[-1]:
                return None

        # ── Get ORB levels ─────────────────────────────────────────────────
        orb_high, orb_low = self._get_orb_levels(df)
        if orb_high is None or orb_low is None:
            logger.debug(f"ORB levels unavailable for symbol {symbol_id} — skipping")
            return None

        # Issue 6 Fix: ORB width filter
        # A very narrow opening range (< orb_min_width_pct of price) is noise,
        # not a meaningful range. Breakouts from micro-ranges produce false signals.
        orb_min_width_pct = self.params.get("orb_min_width_pct", 0.003)  # 0.3%
        mid_price = (orb_high + orb_low) / 2
        orb_width_pct = (orb_high - orb_low) / mid_price if mid_price > 0 else 0.0
        if orb_width_pct < orb_min_width_pct:
            logger.debug(
                f"ORB width too narrow for {symbol_id}: "
                f"{orb_width_pct*100:.2f}% < {orb_min_width_pct*100:.1f}% — skipping"
            )
            return None

        # After the None-guard, these variables are definitely floats.
        # We use cast() because Pyre2 does not narrow after `if x is None: return`.
        orb_high_f: float = cast(float, orb_high)
        orb_low_f: float = cast(float, orb_low)

        # ── Current bar values ─────────────────────────────────────────────
        close_curr   = float(df["close"].iloc[-1])
        high_curr    = float(df["high"].iloc[-1])
        low_curr     = float(df["low"].iloc[-1])
        vwap_curr    = float(df["vwap"].iloc[-1])
        ema_fast     = float(df["ema_fast"].iloc[-1])
        ema_slow     = float(df["ema_slow"].iloc[-1])
        atr_curr     = float(df["atr"].iloc[-1])
        volume_curr  = float(df["volume"].iloc[-1])
        volume_ma    = float(df["volume_ma"].iloc[-1]) if not pd.isna(df["volume_ma"].iloc[-1]) else 0.0
        candle_time  = df.index[-1] if isinstance(df.index, pd.DatetimeIndex) else df.get("time", pd.Series()).iloc[-1]

        atr_sl        = self.params.get("atr_multiplier_sl", 1.0)
        risk_reward   = self.params.get("risk_reward", 2.0)
        volume_factor = self.params.get("volume_factor", 1.0)  # volume must be > volume_factor × volume_ma

        # ── Previous bar (to confirm breakout happened on the candle close) ──
        close_prev = float(df["close"].iloc[-2])

        # ── LONG (BUY) Conditions ──────────────────────────────────────────
        if (
            close_prev <= orb_high_f            # Was at or below ORB high previously
            and close_curr > orb_high_f         # Now broke above ORB high on CLOSE
            and close_curr > vwap_curr          # Price above VWAP
            and ema_fast > ema_slow             # EMA9 > EMA21 (trend up)
            and volume_curr > volume_factor * volume_ma  # Volume spike
        ):
            entry = close_curr
            atr_sl_val = entry - (atr_sl * atr_curr)
            # SL is the tighter of ATR-based SL or orb_low
            stop_loss = max(atr_sl_val, orb_low_f)
            stop_distance = entry - stop_loss
            if stop_distance <= 0:
                stop_loss = entry - (atr_sl * atr_curr)
                stop_distance = entry - stop_loss
            target = entry + (risk_reward * stop_distance)

            logger.info(
                f"ORB+VWAP BUY signal: symbol={symbol_id} entry={entry:.2f} "
                f"ORB_H={orb_high_f:.2f} VWAP={vwap_curr:.2f} SL={stop_loss:.2f} T={target:.2f}"
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
            )

        # ── SHORT (SELL) Conditions ────────────────────────────────────────
        if (
            close_prev >= orb_low_f             # Was at or above ORB low previously
            and close_curr < orb_low_f          # Now broke below ORB low on CLOSE
            and close_curr < vwap_curr          # Price below VWAP
            and ema_fast < ema_slow             # EMA9 < EMA21 (trend down)
            and volume_curr > volume_factor * volume_ma  # Volume spike
        ):
            entry = close_curr
            atr_sl_val = entry + (atr_sl * atr_curr)
            # SL is the tighter of ATR-based SL or orb_high
            stop_loss = min(atr_sl_val, orb_high_f)
            stop_distance = stop_loss - entry
            if stop_distance <= 0:
                stop_loss = entry + (atr_sl * atr_curr)
                stop_distance = stop_loss - entry
            target = entry - (risk_reward * stop_distance)

            logger.info(
                f"ORB+VWAP SELL signal: symbol={symbol_id} entry={entry:.2f} "
                f"ORB_L={orb_low_f:.2f} VWAP={vwap_curr:.2f} SL={stop_loss:.2f} T={target:.2f}"
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
            )

        return None
