"""
RegimeDetector — Classifies the current market day as TREND, RANGE, or HIGH_VOLATILITY.

Used by the scanner to automatically disable strategies that don't suit the day type:
  - TREND day → enable momentum/ORB; disable mean-reversion
  - RANGE day → enable mean-reversion/RSI; disable ORB/breakout
  - HIGH_VOLATILITY → raise confidence threshold; reduce position size suggestion

Detection logic (on 1-min candles for the current session):
  1. ADX proxy via directional move ratio
  2. ATR vs rolling ATR average (volatility expansion)
  3. Net price move vs total range (trend efficiency)
"""
import pandas as pd
import numpy as np

from app.core.logging import logger
from app.engine.indicators import IndicatorEngine


class RegimeDetector:
    """
    Classifies intraday market regime from 1-min OHLCV candles.

    Returns one of: "TREND", "RANGE", "HIGH_VOLATILITY", "LOW_LIQUIDITY"

    Phase 5: Added LOW_LIQUIDITY regime — detected when market_volume_ratio < 0.5.
    When LOW_LIQUIDITY is active, GlobalMarketRegimeFilter blocks all signals.
    """

    def __init__(
        self,
        atr_period: int = 14,
        atr_lookback: int = 20,      # bars for rolling ATR average
        vol_spike_multiplier: float = 2.0,  # ATR > n× avg → HIGH_VOLATILITY
        trend_efficiency_threshold: float = 0.4,  # net move / total range > t → TREND
        range_efficiency_threshold: float = 0.2,  # net move / total range < t → RANGE
    ):
        self.atr_period = atr_period
        self.atr_lookback = atr_lookback
        self.vol_spike_mult = vol_spike_multiplier
        self.trend_eff_thresh = trend_efficiency_threshold
        self.range_eff_thresh = range_efficiency_threshold

    def detect(self, candles: pd.DataFrame, market_volume_ratio: float | None = None) -> str:
        """
        Detect the market regime from OHLCV candles.

        Args:
            candles:              DataFrame with [time, open, high, low, close, volume]
                                  Should contain at least 30 1-min candles of the current session.
            market_volume_ratio:  Optional ratio of today's volume vs 20-day avg.
                                  If < 0.5, returns LOW_LIQUIDITY immediately.

        Returns:
            "TREND" | "RANGE" | "HIGH_VOLATILITY" | "LOW_LIQUIDITY"
        """
        min_bars = self.atr_period + self.atr_lookback + 5
        if candles is None or len(candles) < min_bars:
            logger.debug(f"RegimeDetector: insufficient data ({len(candles) if candles is not None else 0} bars) — returning TREND")
            return "TREND"

        # Phase 5: LOW_LIQUIDITY regime — check before ATR/efficiency analysis
        if market_volume_ratio is not None and market_volume_ratio < 0.5:
            logger.info(f"Regime: LOW_LIQUIDITY (volume_ratio={market_volume_ratio:.2f})")
            return "LOW_LIQUIDITY"

        try:
            atr_series = IndicatorEngine.atr(
                candles["high"], candles["low"], candles["close"], self.atr_period
            )
            current_atr = float(atr_series.iloc[-1]) if not pd.isna(atr_series.iloc[-1]) else 0.0
            avg_atr     = float(atr_series.iloc[-self.atr_lookback:].mean())

            if avg_atr <= 0:
                return "TREND"

            # ── High Volatility: ATR spike ─────────────────────
            if current_atr > self.vol_spike_mult * avg_atr:
                logger.info(f"Regime: HIGH_VOLATILITY (ATR={current_atr:.4f}, avg={avg_atr:.4f})")
                return "HIGH_VOLATILITY"

            # ── Trend Efficiency Ratio ─────────────────────────
            # How much did price actually move (directionally) vs. how much ground was covered?
            window = candles.iloc[-self.atr_lookback:]
            net_move   = abs(float(window["close"].iloc[-1]) - float(window["close"].iloc[0]))
            total_range = float(window["high"].max() - window["low"].min())

            efficiency = net_move / total_range if total_range > 0 else 0.0

            if efficiency >= self.trend_eff_thresh:
                logger.info(f"Regime: TREND (efficiency={efficiency:.2f})")
                return "TREND"
            elif efficiency <= self.range_eff_thresh:
                logger.info(f"Regime: RANGE (efficiency={efficiency:.2f})")
                return "RANGE"
            else:
                # Ambiguous — default to TREND (allow most strategies)
                logger.debug(f"Regime: TREND (ambiguous, efficiency={efficiency:.2f})")
                return "TREND"

        except Exception as e:
            logger.warning(f"RegimeDetector error: {e} — returning TREND")
            return "TREND"

    def get_summary(self, candles: pd.DataFrame) -> dict:
        """Return regime + supporting metrics for display."""
        regime = self.detect(candles)

        # Strategies disabled by regime
        disabled: list[str] = []
        if regime == "RANGE":
            disabled = ["orb_vwap", "volume_expansion"]
        elif regime == "HIGH_VOLATILITY":
            disabled = ["orb_vwap", "volume_expansion", "trend_continuation"]

        return {
            "regime": regime,
            "disabled_strategies": disabled,
            "interpretation": {
                "TREND": "Strong directional day — momentum strategies preferred",
                "RANGE": "Oscillating market — mean-reversion strategies preferred",
                "HIGH_VOLATILITY": "Extreme volatility — reduce size, widen stops",
            }.get(regime, ""),
        }

    # Strategies to disable per regime
    DISABLED_BY_REGIME: dict[str, list[str]] = {
        "RANGE":          ["orb_vwap", "volume_expansion"],
        "HIGH_VOLATILITY": ["orb_vwap", "volume_expansion", "trend_continuation"],
        "TREND":          [],  # All strategies allowed
        # Phase 5: LOW_LIQUIDITY disables ALL strategies — capital protection
        "LOW_LIQUIDITY":  [
            "ema_crossover", "rsi_mean_reversion", "orb_vwap",
            "volume_expansion", "trend_continuation", "failed_breakout",
        ],
    }

    @classmethod
    def is_strategy_allowed(cls, strategy_key: str, regime: str) -> bool:
        """Returns True if the strategy should run in this regime."""
        return strategy_key not in cls.DISABLED_BY_REGIME.get(regime, [])
