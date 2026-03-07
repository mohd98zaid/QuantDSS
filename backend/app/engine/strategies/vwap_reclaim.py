"""
VWAPReclaimStrategy — Institutional re-entry on VWAP cross.

Architecture doc: Section 6.2 — VWAP Reclaim.

Concept:
  Price dips below VWAP (or spikes above), then reclaims it with
  volume — signals institutional re-entry/distribution.

Entry Conditions (LONG):
  1. Previous bar closed BELOW VWAP (price was under)
  2. Current bar closes ABOVE VWAP (reclaim happened)
  3. Relative Volume ≥ 1.3× 20-period average (institutional footprint)
  4. EMA9 > EMA21 (short-term trend aligned bullish)

Entry Conditions (SHORT — inverse):
  1. Previous bar closed ABOVE VWAP
  2. Current bar closes BELOW VWAP (loss of VWAP support)
  3. RelVol ≥ 1.3×
  4. EMA9 < EMA21

Stop Loss:
  LONG:  First candle low that closed below VWAP, capped at 1.5×ATR
  SHORT: First candle high that closed above VWAP, capped at 1.5×ATR

Target:
  2×ATR above/below entry

Best conditions:
  Trend days with clear VWAP as institutional magnet.
  Avoid during HIGH_VOLATILITY (VWAP frequently crossed — noise).
"""

import pandas as pd

from app.core.logging import logger
from app.engine.base_strategy import BaseStrategy, RawSignal
from app.engine.indicators import IndicatorEngine


class VWAPReclaimStrategy(BaseStrategy):
    """
    VWAP Reclaim — Buy/sell the VWAP cross with volume and EMA confirmation.
    """

    @property
    def strategy_type(self) -> str:
        return "vwap_reclaim"

    @property
    def min_candles_required(self) -> int:
        return max(
            self.params.get("ema_slow", 21),
            self.params.get("volume_ma_period", 20),
            self.params.get("atr_period", 14),
        ) + 5

    def evaluate(self, candles: pd.DataFrame, symbol_id: int) -> RawSignal | None:
        """Evaluate VWAP reclaim conditions on last two closed candles."""
        if len(candles) < self.min_candles_required:
            return None

        # Compute indicators if not pre-computed
        if "vwap" not in candles.columns:
            df = IndicatorEngine.compute_strategy_indicators(
                candles, self.strategy_type, self.params
            )
        else:
            df = candles

        # Guard: need valid indicators on current bar
        for col in ("vwap", "ema_fast", "ema_slow", "atr", "volume_ma"):
            if col not in df.columns or pd.isna(df[col].iloc[-1]):
                return None

        curr = df.iloc[-1]
        prev = df.iloc[-2]

        close_curr  = float(curr["close"])
        close_prev  = float(prev["close"])
        vwap_curr   = float(curr["vwap"])
        ema_fast    = float(curr["ema_fast"])
        ema_slow    = float(curr["ema_slow"])
        atr_curr    = float(curr["atr"])
        volume_curr = float(curr["volume"])
        volume_ma   = float(curr["volume_ma"]) if not pd.isna(curr["volume_ma"]) else 0.0

        atr_sl      = self.params.get("atr_multiplier_sl", 1.5)
        atr_target  = self.params.get("atr_multiplier_target", 2.0)
        rvol_thresh = self.params.get("rel_vol_threshold", 1.3)

        rel_vol = (volume_curr / volume_ma) if volume_ma > 0 else 0.0

        candle_time = (
            df.index[-1] if isinstance(df.index, pd.DatetimeIndex)
            else df["time"].iloc[-1]
        )

        # ── LONG: VWAP Reclaim (cross from below to above) ───────────────
        if (
            close_prev < vwap_curr         # Was below VWAP
            and close_curr > vwap_curr     # Now above VWAP (reclaimed)
            and rel_vol >= rvol_thresh     # Volume confirms participation
            and ema_fast > ema_slow        # Short-term trend aligned bullish
        ):
            entry     = close_curr
            stop_loss = max(entry - atr_sl * atr_curr, float(prev["low"]))
            sl_dist   = entry - stop_loss
            if sl_dist <= 0:
                return None
            target = entry + atr_target * atr_curr

            # Confidence: stronger VWAP cross + volume spike = higher score
            score = 50.0
            score += min(15.0, (rel_vol - 1.0) * 10)  # up to +15 for RVOL
            ema_gap = (ema_fast - ema_slow) / ema_slow * 100 if ema_slow > 0 else 0
            score += min(15.0, ema_gap * 200)          # up to +15 for EMA gap
            cross_strength = (close_curr - vwap_curr) / atr_curr * 10
            score += min(15.0, cross_strength)         # up to +15 for cross strength
            score = min(100.0, max(0.0, score))

            logger.info(
                f"VWAPReclaim BUY: symbol={symbol_id} @ {entry:.2f} "
                f"VWAP={vwap_curr:.2f} SL={stop_loss:.2f} T={target:.2f} "
                f"RVOL={rel_vol:.2f} conf={score:.0f}"
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
                confidence_score=score,
            )

        # ── SHORT: VWAP Loss (cross from above to below) ─────────────────
        if (
            close_prev > vwap_curr         # Was above VWAP
            and close_curr < vwap_curr     # Now below VWAP (lost support)
            and rel_vol >= rvol_thresh
            and ema_fast < ema_slow        # Short-term trend aligned bearish
        ):
            entry     = close_curr
            stop_loss = min(entry + atr_sl * atr_curr, float(prev["high"]))
            sl_dist   = stop_loss - entry
            if sl_dist <= 0:
                return None
            target = entry - atr_target * atr_curr

            score = 50.0
            score += min(15.0, (rel_vol - 1.0) * 10)
            ema_gap = (ema_slow - ema_fast) / ema_slow * 100 if ema_slow > 0 else 0
            score += min(15.0, ema_gap * 200)
            cross_strength = (vwap_curr - close_curr) / atr_curr * 10
            score += min(15.0, cross_strength)
            score = min(100.0, max(0.0, score))

            logger.info(
                f"VWAPReclaim SELL: symbol={symbol_id} @ {entry:.2f} "
                f"VWAP={vwap_curr:.2f} SL={stop_loss:.2f} T={target:.2f} "
                f"RVOL={rel_vol:.2f} conf={score:.0f}"
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
                confidence_score=score,
            )

        return None
