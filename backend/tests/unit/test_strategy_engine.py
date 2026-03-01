"""
Unit tests for Strategy Engine — EMA Crossover and RSI Mean Reversion.
"""
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd

from app.engine.indicators import IndicatorEngine
from app.engine.strategies.ema_crossover import EMACrossoverStrategy
from app.engine.strategies.rsi_mean_reversion import RSIMeanReversionStrategy


def make_candles(n: int = 100, trend: str = "up") -> pd.DataFrame:
    """Generate synthetic OHLCV data for testing."""
    np.random.seed(42)
    dates = [datetime(2024, 1, 1, tzinfo=UTC) + timedelta(minutes=i) for i in range(n)]

    if trend == "up":
        base = 100 + np.cumsum(np.random.randn(n) * 0.5 + 0.1)
    elif trend == "down":
        base = 200 - np.cumsum(np.random.randn(n) * 0.5 + 0.1)
    else:
        base = 150 + np.cumsum(np.random.randn(n) * 0.5)

    noise = np.random.randn(n) * 0.3
    high = base + abs(noise) + 0.5
    low = base - abs(noise) - 0.5

    df = pd.DataFrame({
        "time": dates,
        "open": base + noise * 0.5,
        "high": high,
        "low": low,
        "close": base,
        "volume": np.random.randint(10000, 100000, n),
    })
    return df


# ─── Indicator Engine Tests ──────────────────────────────────

class TestIndicatorEngine:
    def test_ema(self):
        candles = make_candles()
        result = IndicatorEngine.ema(candles["close"], 9)
        assert len(result) == len(candles)
        # First 8 values should be NaN
        assert result.iloc[:8].isna().sum() >= 7

    def test_rsi(self):
        candles = make_candles()
        result = IndicatorEngine.rsi(candles["close"], 14)
        assert len(result) == len(candles)

    def test_atr(self):
        candles = make_candles()
        result = IndicatorEngine.atr(candles["high"], candles["low"], candles["close"], 14)
        assert len(result) == len(candles)
        # ATR should be positive where computed
        valid = result.dropna()
        assert (valid >= 0).all()

    def test_volume_ma(self):
        candles = make_candles()
        result = IndicatorEngine.volume_ma(candles["volume"], 20)
        assert len(result) == len(candles)

    def test_compute_ema_crossover_indicators(self):
        candles = make_candles()
        params = {"ema_fast": 9, "ema_slow": 21, "atr_period": 14, "volume_ma_period": 20}
        df = IndicatorEngine.compute_strategy_indicators(candles, "ema_crossover", params)
        assert "ema_fast" in df.columns
        assert "ema_slow" in df.columns
        assert "atr" in df.columns
        assert "volume_ma" in df.columns

    def test_compute_rsi_mr_indicators(self):
        candles = make_candles()
        params = {"rsi_period": 14, "ema_trend": 50, "atr_period": 14}
        df = IndicatorEngine.compute_strategy_indicators(candles, "rsi_mean_reversion", params)
        assert "rsi" in df.columns
        assert "ema_trend" in df.columns
        assert "atr" in df.columns


# ─── EMA Crossover Strategy Tests ────────────────────────────

class TestEMACrossover:
    def test_returns_none_insufficient_candles(self):
        strategy = EMACrossoverStrategy(
            strategy_id=1,
            params={"ema_fast": 9, "ema_slow": 21, "atr_period": 14, "volume_ma_period": 20,
                     "atr_multiplier_sl": 1.5, "atr_multiplier_target": 3.0},
        )
        candles = make_candles(10)  # Too few
        result = strategy.evaluate(candles, symbol_id=1)
        assert result is None

    def test_min_candles_required(self):
        strategy = EMACrossoverStrategy(
            strategy_id=1,
            params={"ema_fast": 9, "ema_slow": 21, "atr_period": 14, "volume_ma_period": 20},
        )
        assert strategy.min_candles_required >= 21

    def test_strategy_type(self):
        strategy = EMACrossoverStrategy(strategy_id=1, params={})
        assert strategy.strategy_type == "ema_crossover"


# ─── RSI Mean Reversion Strategy Tests ───────────────────────

class TestRSIMeanReversion:
    def test_returns_none_insufficient_candles(self):
        strategy = RSIMeanReversionStrategy(
            strategy_id=2,
            params={"rsi_period": 14, "rsi_oversold": 35, "rsi_overbought": 65,
                     "ema_trend": 50, "atr_period": 14, "atr_multiplier_sl": 1.0,
                     "risk_reward": 2.0},
        )
        candles = make_candles(10)
        result = strategy.evaluate(candles, symbol_id=1)
        assert result is None

    def test_min_candles_required(self):
        strategy = RSIMeanReversionStrategy(
            strategy_id=2,
            params={"rsi_period": 14, "ema_trend": 50, "atr_period": 14},
        )
        assert strategy.min_candles_required >= 50

    def test_strategy_type(self):
        strategy = RSIMeanReversionStrategy(strategy_id=2, params={})
        assert strategy.strategy_type == "rsi_mean_reversion"
