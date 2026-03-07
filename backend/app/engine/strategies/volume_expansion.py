"""
Volume Expansion Strategy — detect abnormal volume surges with price range breakouts.

Entry conditions:
  LONG:  Volume > vol_multiplier × 20-bar avg  +  close > 5-bar high  +  ATR expanding
  SHORT: Volume > vol_multiplier × 20-bar avg  +  close < 5-bar low   +  ATR expanding

Fast momentum — target 1:1.5 R:R; SL below breakout candle low/high.
"""
import pandas as pd

from app.core.logging import logger
from app.engine.base_strategy import BaseStrategy, CandidateSignal
from app.engine.indicators import IndicatorEngine


class VolumeExpansionStrategy(BaseStrategy):
    """
    Volume Expansion — momentum breakout confirmed by abnormal volume.

    Signals when:
      1. Volume spikes above vol_multiplier × 20-bar average (default 3×)
      2. Price breaks out of 5-bar high (LONG) or 5-bar low (SHORT)
      3. ATR is expanding (current > previous)
    """

    @property
    def strategy_type(self) -> str:
        return "volume_expansion"

    @property
    def min_candles_required(self) -> int:
        return max(
            self.params.get("volume_ma_period", 20),
            self.params.get("lookback_bars", 5),
            self.params.get("atr_period", 14),
        ) + 5

    def evaluate(self, candles: pd.DataFrame, symbol_id: int) -> CandidateSignal | None:
        """Evaluate volume expansion conditions."""
        if len(candles) < self.min_candles_required:
            return None

        if "volume_ma" not in candles.columns:
            df = IndicatorEngine.compute_strategy_indicators(
                candles, self.strategy_type, self.params
            )
        else:
            df = candles

        if df["atr"].isna().iloc[-1] or df["volume_ma"].isna().iloc[-1]:
            return None

        vol_mult  = self.params.get("vol_multiplier", 3.0)
        lookback  = int(self.params.get("lookback_bars", 5))
        atr_sl    = self.params.get("atr_multiplier_sl", 1.0)
        rr_ratio  = self.params.get("risk_reward", 1.5)

        curr      = df.iloc[-1]
        prev      = df.iloc[-2]
        window    = df.iloc[-(lookback + 1):-1]

        volume_curr   = float(curr["volume"])
        volume_ma     = float(curr["volume_ma"])
        close_curr    = float(curr["close"])
        high_curr     = float(curr["high"])
        low_curr      = float(curr["low"])
        atr_curr      = float(curr["atr"])
        atr_prev      = float(prev["atr"]) if not pd.isna(prev["atr"]) else atr_curr
        candle_time   = df.index[-1] if isinstance(df.index, pd.DatetimeIndex) else df["time"].iloc[-1]

        # Volume spike check
        if volume_ma <= 0 or volume_curr < vol_mult * volume_ma:
            return None

        # ATR expansion check
        atr_expanding = atr_curr > atr_prev

        rvol = volume_curr / volume_ma
        # Confidence: base 50 + volume ratio + ATR expanding
        score = 50.0 + min(30.0, (rvol - vol_mult) * 10.0) + (20.0 if atr_expanding else 0.0)
        score = min(100.0, max(0.0, score))

        # ─── LONG: breakout above 5-bar high ────────────────────
        if len(window) >= lookback:
            range_high = float(window["high"].max())
            range_low  = float(window["low"].min())

            open_curr = float(curr["open"])

            # Issue 6 Fix: Price confirmation required for Volume Expansion.
            # A volume spike with no directional price close is institutional
            # activity that may be the other side of the trade. We require:
            #   LONG:  close > open  (bullish candle body)
            #   SHORT: close < open  (bearish candle body)
            if close_curr > range_high and close_curr > open_curr:  # bullish body
                entry     = close_curr
                stop_loss = low_curr - (atr_sl * atr_curr)
                sl_dist   = entry - stop_loss
                target    = entry + sl_dist * rr_ratio

                logger.info(
                    f"VolumeExpansion BUY: {symbol_id} @ {entry:.2f} "
                    f"RVol={rvol:.1f}x SL={stop_loss:.2f} T={target:.2f} conf={score:.0f}"
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

            # ─── SHORT: breakout below 5-bar low ────────────────
            elif close_curr < range_low and close_curr < open_curr:  # bearish body
                entry     = close_curr
                stop_loss = high_curr + (atr_sl * atr_curr)
                sl_dist   = stop_loss - entry
                target    = entry - sl_dist * rr_ratio

                logger.info(
                    f"VolumeExpansion SELL: {symbol_id} @ {entry:.2f} "
                    f"RVol={rvol:.1f}x SL={stop_loss:.2f} T={target:.2f} conf={score:.0f}"
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
