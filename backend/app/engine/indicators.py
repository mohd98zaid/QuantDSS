"""
IndicatorEngine — Vectorised technical indicator computation.
Uses the `ta` library (pure Python, no C compilation needed).
Performance target: < 50ms for 100 candles.
"""
import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator, SMAIndicator
from ta.volatility import AverageTrueRange, BollingerBands


class IndicatorEngine:
    """Stateless technical indicator calculator using the `ta` library."""

    @staticmethod
    def ema(series: pd.Series, period: int) -> pd.Series:
        """Exponential Moving Average."""
        return EMAIndicator(close=series, window=period).ema_indicator()

    @staticmethod
    def sma(series: pd.Series, period: int) -> pd.Series:
        """Simple Moving Average."""
        return SMAIndicator(close=series, window=period).sma_indicator()

    @staticmethod
    def rsi(series: pd.Series, period: int) -> pd.Series:
        """Relative Strength Index."""
        return RSIIndicator(close=series, window=period).rsi()

    @staticmethod
    def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
        """Average True Range."""
        return AverageTrueRange(high=high, low=low, close=close, window=period).average_true_range()

    @staticmethod
    def volume_ma(volume: pd.Series, period: int) -> pd.Series:
        """Volume Moving Average (SMA of volume)."""
        return SMAIndicator(close=volume, window=period).sma_indicator()

    @staticmethod
    def bollinger_bands(series: pd.Series, period: int, std_dev: float = 2.0):
        """Bollinger Bands — returns dict with 'upper', 'middle', 'lower'."""
        bb = BollingerBands(close=series, window=period, window_dev=std_dev)
        return {
            "upper": bb.bollinger_hband(),
            "middle": bb.bollinger_mavg(),
            "lower": bb.bollinger_lband(),
        }

    @classmethod
    def compute_strategy_indicators(
        cls,
        df: pd.DataFrame,
        strategy_type: str,
        params: dict,
    ) -> pd.DataFrame:
        """
        Compute all indicators required for a strategy and add them as columns.

        Args:
            df: OHLCV DataFrame with columns: open, high, low, close, volume
            strategy_type: 'ema_crossover' or 'rsi_mean_reversion'
            params: Strategy parameters dict

        Returns:
            DataFrame with added indicator columns
        """
        df = df.copy()

        if strategy_type == "ema_crossover":
            df["ema_fast"] = cls.ema(df["close"], params.get("ema_fast", 9))
            df["ema_slow"] = cls.ema(df["close"], params.get("ema_slow", 21))
            df["atr"] = cls.atr(df["high"], df["low"], df["close"], params.get("atr_period", 14))
            df["volume_ma"] = cls.volume_ma(df["volume"], params.get("volume_ma_period", 20))

        elif strategy_type == "rsi_mean_reversion":
            df["rsi"] = cls.rsi(df["close"], params.get("rsi_period", 14))
            df["ema_trend"] = cls.ema(df["close"], params.get("ema_trend", 50))
            df["atr"] = cls.atr(df["high"], df["low"], df["close"], params.get("atr_period", 14))

        return df
