"""
IndicatorEngine — Vectorised technical indicator computation.
Uses the `ta` library (pure Python, no C compilation needed).
Performance target: < 50ms for 100 candles.

Additions (Audit Phase 12):
  - macd(): MACD line, Signal line, and Histogram added
  - compute_for_strategy(): ema_crossover and trend_continuation strategies
    now include MACD as a confirmation indicator

Fix 2 (VWAP Day Reset):
  - vwap() rewritten to group candles by trading date using pd.Grouper or .dt.date.
  - Previous implementation compared timestamp == 09:15, so a single missing candle
    caused cross-day VWAP corruption for the entire subsequent day.
  - New approach: typicalprice * volume is grouped by date, cumsum per group,
    then VWAP = cum(tp*vol) / cum(vol). Resets cleanly on every new calendar date.
"""
import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator, SMAIndicator, MACD
from ta.volatility import AverageTrueRange, BollingerBands
from datetime import time as _dt_time


def _cls_compute_orb_columns(df: pd.DataFrame, params: dict) -> None:
    """
    Issue 5 Fix — Compute Opening Range (ORB) High/Low columns correctly.

    PROBLEM (pre-fix):
        The old code assigned or_high and or_low as scalars across ALL rows:
            df["or_high"] = or_high   # wrong: pre-ORB bars also have this value
        This allowed the ORBVWAPStrategy to fire a BUY/SELL signal even when
        the last candle was at 09:18 (before the ORB window closes at 09:30).

    FIX:
        - or_high and or_low are initialised to NaN for every row.
        - After computing the ORB range from 09:15–09:30 candles, the values are
          stamped ONLY on rows with index.time > 09:30.
        - ORBVWAPStrategy.evaluate() already guards with `isna()` on or_high
          for the last row — so NaN on pre-ORB rows correctly suppresses signals.

    Fallback for backtest (non-DatetimeIndex): stamps the first 15 bars as NaN
    and all subsequent bars with the ORB levels derived from those first 15 bars.

    Args:
        df:     DataFrame with OHLCV columns (modified in-place)
        params: Strategy params dict (unused currently, reserved for custom ORB window)
    """
    orb_start = _dt_time(9, 15)
    orb_end   = _dt_time(9, 30)

    # Initialise both columns to NaN — default for all bars
    df["or_high"] = float("nan")
    df["or_low"]  = float("nan")

    if isinstance(df.index, pd.DatetimeIndex) and df.index.tz is not None:
        idx_time = df.index.tz_convert("Asia/Kolkata").time
        # Bars inside the ORB window (09:15 ≤ t ≤ 09:30)
        in_orb = pd.Series(
            [(t >= orb_start and t <= orb_end) for t in idx_time],
            index=df.index
        )
        or_slice = df.loc[in_orb]
        if or_slice.empty:
            return  # No ORB data available; leave NaN so strategy skips signal

        or_high_val = float(or_slice["high"].max())
        or_low_val  = float(or_slice["low"].min())

        # Only apply levels to bars AFTER the ORB window has closed (> 09:30)
        post_orb = pd.Series(
            [t > orb_end for t in idx_time],
            index=df.index
        )
        df.loc[post_orb, "or_high"] = or_high_val
        df.loc[post_orb, "or_low"]  = or_low_val

    elif isinstance(df.index, pd.DatetimeIndex) and df.index.tz is None:
        # Timezone-naive fallback (assumes times are already in IST)
        idx_time = df.index.time
        in_orb = pd.Series(
            [(t >= orb_start and t <= orb_end) for t in idx_time],
            index=df.index
        )
        or_slice = df.loc[in_orb]
        if or_slice.empty:
            return
        or_high_val = float(or_slice["high"].max())
        or_low_val  = float(or_slice["low"].min())
        post_orb = pd.Series(
            [t > orb_end for t in idx_time],
            index=df.index
        )
        df.loc[post_orb, "or_high"] = or_high_val
        df.loc[post_orb, "or_low"]  = or_low_val

    else:
        # Integer-indexed fallback (backtests without datetime index):
        # treat first 15 rows as ORB, all subsequent rows as post-ORB.
        n_orb_bars = 15
        if len(df) <= n_orb_bars:
            return   # Not enough data
        or_slice    = df.iloc[:n_orb_bars]
        or_high_val = float(or_slice["high"].max())
        or_low_val  = float(or_slice["low"].min())
        df.iloc[n_orb_bars:, df.columns.get_loc("or_high")] = or_high_val
        df.iloc[n_orb_bars:, df.columns.get_loc("or_low")]  = or_low_val


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
    def macd(
        series: pd.Series,
        fast: int = 12,
        slow: int = 26,
        signal: int = 9,
    ) -> tuple[pd.Series, pd.Series, pd.Series]:
        """
        Moving Average Convergence Divergence.
        Fix (Audit Cat. 2): MACD was missing from the indicator library.

        Returns:
            (macd_line, signal_line, histogram)
        """
        m = MACD(close=series, window_fast=fast, window_slow=slow, window_sign=signal)
        return m.macd(), m.macd_signal(), m.macd_diff()

    @staticmethod
    def vwap(
        high: pd.Series,
        low: pd.Series,
        close: pd.Series,
        volume: pd.Series,
        session_start_hour: int = 9,
        session_start_minute: int = 15,
    ) -> pd.Series:
        """
        Volume Weighted Average Price (VWAP).

        Fix 2: Rewritten to use date-based grouping instead of timestamp equality.

        PREVIOUS BUG:
            The old code detected session start with `df.index.time == dt_time(9,15)`
            and then used cumsum() groups based on that flag. If the 09:15 candle was
            missing (e.g. late subscription or feed gap), the VWAP never reset and
            accumulated data across days — a purely corrupting artefact.

        FIX:
            Group candles by their calendar date (`.dt.date`) and compute cumulative
            weighted sum within each group. VWAP resets at every new trading date,
            regardless of whether the 09:15 candle is present.

        Formula:
            typical_price = (high + low + close) / 3
            vwap = cumsum(typical_price * volume) / cumsum(volume)  [per day]
        """
        # Fix 6: Use "time" column if index is not DatetimeIndex
        df = pd.DataFrame({"high": high, "low": low, "close": close, "volume": volume})
        
        if isinstance(high.index, pd.DatetimeIndex):
            df.index = high.index
            time_series = pd.Series(df.index, index=df.index)
        elif "time" in df.columns:
            time_series = pd.to_datetime(df["time"])
            df.index = time_series
        else:
            time_series = None

        typical = (df["high"] + df["low"] + df["close"]) / 3
        df["tp_vol"] = typical * df["volume"]

        if time_series is not None and hasattr(time_series.dt, "date"):
            # Preferred path: group by calendar date — works even if 09:15 is missing
            if df.index.tz is not None:
                date_key = time_series.dt.tz_convert("Asia/Kolkata").dt.date
            else:
                date_key = time_series.dt.date

            df["_date"] = date_key
            df["cum_tp_vol"] = df.groupby("_date")["tp_vol"].cumsum()
            df["cum_vol"]    = df.groupby("_date")["volume"].cumsum()
        else:
            # Fallback for integer-indexed data without time column (backtests)
            df["cum_tp_vol"] = df["tp_vol"].cumsum()
            df["cum_vol"]    = df["volume"].cumsum()

        # Avoid division by zero on zero-volume bars
        vwap = df["cum_tp_vol"] / df["cum_vol"].replace(0, float("nan"))
        return vwap.reindex(high.index)

    @staticmethod
    def bollinger_bands(
        series: pd.Series,
        period: int = 20,
        std_dev: float = 2.0,
    ) -> tuple[pd.Series, pd.Series, pd.Series]:
        """
        Bollinger Bands — upper, middle (SMA), lower bands.

        Returns:
            (upper_band, middle_band, lower_band)
        """
        bb = BollingerBands(close=series, window=period, window_dev=std_dev)
        return bb.bollinger_hband(), bb.bollinger_mavg(), bb.bollinger_lband()

    @classmethod
    def compute_for_strategy(
        cls,
        df: pd.DataFrame,
        strategy_type: str,
        params: dict | None = None,
    ) -> pd.DataFrame:
        """
        Compute all indicators needed by a specific strategy.

        Args:
            df:             OHLCV DataFrame with columns: open, high, low, close, volume
            strategy_type:  Strategy identifier string
            params:         Strategy-specific parameters dict

        Returns:
            DataFrame with indicator columns added in-place.
        """
        if params is None:
            params = {}

        if strategy_type == "ema_crossover":
            df["ema_fast"] = cls.ema(df["close"], params.get("ema_fast", 9))
            df["ema_slow"] = cls.ema(df["close"], params.get("ema_slow", 21))
            df["rsi"]      = cls.rsi(df["close"], params.get("rsi_period", 14))
            df["atr"]      = cls.atr(df["high"], df["low"], df["close"], params.get("atr_period", 14))
            df["volume_ma"] = cls.volume_ma(df["volume"], params.get("volume_ma_period", 20))
            # MACD as confirmation (Audit fix)
            df["macd"], df["macd_signal"], df["macd_hist"] = cls.macd(df["close"])

        elif strategy_type == "rsi_mean_reversion":
            df["rsi"]       = cls.rsi(df["close"], params.get("rsi_period", 14))
            df["ema_trend"] = cls.ema(df["close"], params.get("ema_trend", 50))
            df["atr"]       = cls.atr(df["high"], df["low"], df["close"], params.get("atr_period", 14))

        elif strategy_type == "orb_vwap":
            df["vwap"]     = cls.vwap(df["high"], df["low"], df["close"], df["volume"])
            df["ema_fast"] = cls.ema(df["close"], params.get("ema_fast", 9))
            df["ema_slow"] = cls.ema(df["close"], params.get("ema_slow", 21))
            df["ema_50"]   = cls.ema(df["close"], 50)
            df["atr"]      = cls.atr(df["high"], df["low"], df["close"], params.get("atr_period", 14))
            df["volume_ma"] = cls.volume_ma(df["volume"], params.get("volume_ma_period", 10))
            # Issue 5 Fix: ORB levels are NaN for all bars during/before the window.
            # Only post-ORB bars carry or_high / or_low so that the strategy cannot
            # fire a breakout signal before the opening range is complete.
            _cls_compute_orb_columns(df, params)

        elif strategy_type == "volume_expansion":
            df["volume_ma"] = cls.volume_ma(df["volume"], params.get("volume_ma_period", 20))
            df["atr"]       = cls.atr(df["high"], df["low"], df["close"], params.get("atr_period", 14))

        elif strategy_type == "trend_continuation":
            df["ema_9"]    = cls.ema(df["close"], 9)
            df["ema_21"]   = cls.ema(df["close"], 21)
            df["ema_50"]   = cls.ema(df["close"], params.get("ema_slow", 50))
            df["vwap"]     = cls.vwap(df["high"], df["low"], df["close"], df["volume"])
            df["atr"]      = cls.atr(df["high"], df["low"], df["close"], params.get("atr_period", 14))
            # MACD as confirmation (Audit fix)
            df["macd"], df["macd_signal"], df["macd_hist"] = cls.macd(df["close"])

        elif strategy_type == "failed_breakout":
            # Phase 3: Failed Breakout Strategy
            df["vwap"]      = cls.vwap(df["high"], df["low"], df["close"], df["volume"])
            df["rsi"]       = cls.rsi(df["close"], params.get("rsi_period", 14))
            df["atr"]       = cls.atr(df["high"], df["low"], df["close"], params.get("atr_period", 14))
            df["volume_ma"] = cls.volume_ma(df["volume"], params.get("volume_ma_period", 20))
            # Issue 5 Fix: Same ORB correction applied to failed_breakout.
            _cls_compute_orb_columns(df, params)

        elif strategy_type == "vwap_reclaim":
            # §6.2 — VWAP Reclaim Strategy
            df["vwap"]      = cls.vwap(df["high"], df["low"], df["close"], df["volume"])
            df["ema_fast"]  = cls.ema(df["close"], params.get("ema_fast", 9))
            df["ema_slow"]  = cls.ema(df["close"], params.get("ema_slow", 21))
            df["atr"]       = cls.atr(df["high"], df["low"], df["close"], params.get("atr_period", 14))
            df["volume_ma"] = cls.volume_ma(df["volume"], params.get("volume_ma_period", 20))

        elif strategy_type == "trend_pullback":
            # §6.3 — Trend Pullback (EMA21 pullback with RSI 40-60)
            df["ema_9"]     = cls.ema(df["close"], 9)
            df["ema_21"]    = cls.ema(df["close"], 21)
            df["ema_50"]    = cls.ema(df["close"], params.get("ema_slow", 50))
            df["rsi"]       = cls.rsi(df["close"], params.get("rsi_period", 14))
            df["atr"]       = cls.atr(df["high"], df["low"], df["close"], params.get("atr_period", 14))

        elif strategy_type == "relative_strength":
            # §6.4 — Relative Strength Strategy
            df["vwap"]      = cls.vwap(df["high"], df["low"], df["close"], df["volume"])
            df["atr"]       = cls.atr(df["high"], df["low"], df["close"], params.get("atr_period", 14))
            df["volume_ma"] = cls.volume_ma(df["volume"], params.get("volume_ma_period", 20))
            # Note: nifty_return_1h is injected via params at pipeline time, not computed here

        return df

    # Alias — some callers use the old name; keep both to avoid breaking changes
    compute_strategy_indicators = compute_for_strategy
