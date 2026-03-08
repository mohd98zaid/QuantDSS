"""
Feature Pipeline — Computes ML features from candle data.

Extracts:
  - Indicator features (EMA, RSI, ATR, MACD, VWAP)
  - Volatility features (Bollinger, ATR%)
  - Regime features (trend strength, regime classification)
  - Liquidity features (volume ratio, spread)
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import numpy as np
import pandas as pd

from app.core.logging import logger


class FeaturePipeline:
    """Compute features from candle DataFrames."""

    @staticmethod
    def extract_features(df: pd.DataFrame, symbol: str = "") -> Optional[dict]:
        """
        Extract a feature vector from the latest candle with lookback context.

        Args:
            df: OHLCV DataFrame with at least 30 rows
            symbol: Trading symbol

        Returns:
            dict of feature values, or None if insufficient data
        """
        if df is None or len(df) < 30:
            return None

        try:
            close = df["close"].values
            high = df["high"].values
            low = df["low"].values
            volume = df["volume"].values

            # EMA
            ema_9 = FeaturePipeline._ema(close, 9)
            ema_21 = FeaturePipeline._ema(close, 21)

            # RSI
            rsi_14 = FeaturePipeline._rsi(close, 14)

            # ATR
            atr_14 = FeaturePipeline._atr(high, low, close, 14)

            # MACD
            ema_12 = FeaturePipeline._ema(close, 12)
            ema_26 = FeaturePipeline._ema(close, 26)
            macd_line = ema_12 - ema_26
            macd_signal = FeaturePipeline._ema(
                np.full(len(close), macd_line), 9
            ) if not np.isnan(macd_line) else 0.0

            # Bollinger Bands
            sma_20 = np.mean(close[-20:])
            std_20 = np.std(close[-20:])
            bb_upper = sma_20 + 2 * std_20
            bb_lower = sma_20 - 2 * std_20

            # Volatility
            returns = np.diff(close[-21:]) / close[-21:-1]
            volatility_pct = float(np.std(returns) * 100) if len(returns) > 0 else 0.0
            atr_pct = (atr_14 / close[-1] * 100) if close[-1] > 0 else 0.0

            # Volume ratio
            avg_vol = np.mean(volume[-20:]) if len(volume) >= 20 else np.mean(volume)
            vol_ratio = volume[-1] / avg_vol if avg_vol > 0 else 1.0

            # Trend strength (ADX simplified: EMA slope)
            trend_strength = (ema_9 - ema_21) / close[-1] * 100 if close[-1] > 0 else 0.0

            # Regime classification
            if abs(trend_strength) > 0.5 and volatility_pct < 2:
                regime = "TREND"
            elif volatility_pct > 3:
                regime = "HIGH_VOLATILITY"
            elif vol_ratio < 0.5:
                regime = "LOW_LIQUIDITY"
            else:
                regime = "RANGE"

            return {
                "symbol": symbol,
                "timestamp": datetime.now(timezone.utc),
                "ema_9": round(float(ema_9), 4),
                "ema_21": round(float(ema_21), 4),
                "rsi_14": round(float(rsi_14), 2),
                "atr_14": round(float(atr_14), 4),
                "macd_line": round(float(macd_line), 4),
                "macd_signal": round(float(macd_signal), 4),
                "vwap": 0.0,  # Computed separately if tick data available
                "bollinger_upper": round(float(bb_upper), 4),
                "bollinger_lower": round(float(bb_lower), 4),
                "volatility_pct": round(volatility_pct, 4),
                "atr_pct": round(atr_pct, 4),
                "regime": regime,
                "trend_strength": round(float(trend_strength), 4),
                "volume": int(volume[-1]),
                "volume_ratio": round(float(vol_ratio), 4),
                "spread_pct": 0.0,
            }

        except Exception as e:
            logger.exception(f"FeaturePipeline: Feature extraction failed for {symbol}: {e}")
            return None

    @staticmethod
    def _ema(data: np.ndarray, period: int) -> float:
        """Compute EMA for the latest value."""
        if len(data) < period:
            return float(np.mean(data))
        alpha = 2 / (period + 1)
        ema = data[0]
        for val in data[1:]:
            ema = alpha * val + (1 - alpha) * ema
        return float(ema)

    @staticmethod
    def _rsi(close: np.ndarray, period: int = 14) -> float:
        """Compute RSI for the latest value."""
        if len(close) < period + 1:
            return 50.0
        deltas = np.diff(close[-(period + 1):])
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        avg_gain = np.mean(gains)
        avg_loss = np.mean(losses)
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return float(100 - (100 / (1 + rs)))

    @staticmethod
    def _atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> float:
        """Compute ATR for the latest value."""
        if len(high) < period + 1:
            return float(np.mean(high - low))
        tr_list = []
        for i in range(1, len(high)):
            tr = max(
                high[i] - low[i],
                abs(high[i] - close[i - 1]),
                abs(low[i] - close[i - 1]),
            )
            tr_list.append(tr)
        if not tr_list:
            return 0.0
        return float(np.mean(tr_list[-period:]))
