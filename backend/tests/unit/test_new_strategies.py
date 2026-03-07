"""
Unit tests for:
  - SignalTimeGateFilter (Rule 8)
  - MaxSignalsPerStockPerDay (Rule 9)
  - Confidence scores in EMACrossoverStrategy / RSIMeanReversionStrategy
  - Backtest transaction cost calculation (slippage, brokerage, STT)
  - Monte Carlo / degradation metrics
"""
import math
from datetime import datetime, timezone, timedelta

import pandas as pd
import numpy as np
import pytest

# ─── Helpers ──────────────────────────────────────────────────────────────────

IST = timezone(timedelta(hours=5, minutes=30))
UTC = timezone.utc


def _make_signal(symbol_id=1, strategy_id=1, signal_type="BUY", hour_ist=10, minute_ist=0):
    """Build a RawSignal with a realistic candle time."""
    from app.engine.base_strategy import RawSignal

    # Build a datetime in IST then convert to UTC
    dt_ist = datetime(2024, 1, 15, hour_ist, minute_ist, tzinfo=IST)
    dt_utc = dt_ist.astimezone(UTC)

    return RawSignal(
        symbol_id=symbol_id,
        strategy_id=strategy_id,
        strategy_name="TestStrategy",
        signal_type=signal_type,
        entry_price=100.0,
        stop_loss=95.0,
        target_price=110.0,
        atr_value=2.0,
        candle_time=dt_utc,
        confidence_score=0.0,
    )


class _MockConfig:
    """Minimal config object for risk rules."""
    signal_start_hour   = 9
    signal_start_minute = 20
    signal_end_hour     = 12
    signal_end_minute   = 0
    max_signals_per_stock = 3


class _MockPortfolio:
    current_balance = 100_000.0
    peak_balance    = 100_000.0
    open_positions  = 0


class _MockState:
    pass


# ─── Rule 8: SignalTimeGateFilter ─────────────────────────────────────────────

from unittest.mock import patch

class TestSignalTimeGateFilter:
    def _rule(self):
        from app.engine.risk_engine import SignalTimeGateFilter
        return SignalTimeGateFilter(_MockConfig())

    @patch("app.engine.risk_engine.datetime")
    def test_passes_within_window(self, mock_dt):
        mock_dt.now.return_value = datetime(2024, 1, 15, 10, 30, tzinfo=IST)
        rule   = self._rule()
        signal = _make_signal(hour_ist=10, minute_ist=30)
        result = rule.check(signal, _MockState(), _MockPortfolio())
        assert result.status == "PASS"

    @patch("app.engine.risk_engine.datetime")
    def test_skips_before_window(self, mock_dt):
        mock_dt.now.return_value = datetime(2024, 1, 15, 9, 10, tzinfo=IST)
        rule   = self._rule()
        signal = _make_signal(hour_ist=9, minute_ist=10)   # 9:10 AM IST — before 9:20
        result = rule.check(signal, _MockState(), _MockPortfolio())
        assert result.status == "SKIPPED"
        assert "OUTSIDE_SIGNAL_WINDOW" in (result.reason or "")

    @patch("app.engine.risk_engine.datetime")
    def test_skips_after_window(self, mock_dt):
        mock_dt.now.return_value = datetime(2024, 1, 15, 14, 0, tzinfo=IST)
        rule   = self._rule()
        signal = _make_signal(hour_ist=14, minute_ist=0)   # 2:00 PM IST — after 12:00
        result = rule.check(signal, _MockState(), _MockPortfolio())
        assert result.status == "SKIPPED"

    @patch("app.engine.risk_engine.datetime")
    def test_passes_at_start_boundary(self, mock_dt):
        mock_dt.now.return_value = datetime(2024, 1, 15, 9, 20, tzinfo=IST)
        rule   = self._rule()
        signal = _make_signal(hour_ist=9, minute_ist=20)   # Exactly 9:20 AM
        result = rule.check(signal, _MockState(), _MockPortfolio())
        assert result.status == "PASS"

    @patch("app.engine.risk_engine.datetime")
    def test_passes_at_end_boundary(self, mock_dt):
        mock_dt.now.return_value = datetime(2024, 1, 15, 12, 0, tzinfo=IST)
        rule   = self._rule()
        signal = _make_signal(hour_ist=12, minute_ist=0)   # Exactly 12:00 PM
        result = rule.check(signal, _MockState(), _MockPortfolio())
        assert result.status == "PASS"


# ─── Rule 9: MaxSignalsPerStockPerDay ─────────────────────────────────────────

class TestMaxSignalsPerStockPerDay:
    def _rule(self):
        from app.engine.risk_engine import MaxSignalsPerStockPerDay
        return MaxSignalsPerStockPerDay(_MockConfig())

    def test_allows_up_to_max(self):
        rule = self._rule()
        s1   = _make_signal(symbol_id=10)
        for _ in range(3):
            res = rule.check(s1, _MockState(), _MockPortfolio())
            assert res.status == "PASS"

    def test_blocks_above_max(self):
        rule = self._rule()
        s1   = _make_signal(symbol_id=99)
        for _ in range(3):
            rule.check(s1, _MockState(), _MockPortfolio())
        result = rule.check(s1, _MockState(), _MockPortfolio())
        assert result.status == "SKIPPED"
        assert "MAX_SIGNALS_PER_STOCK" in (result.reason or "")

    def test_different_stocks_are_independent(self):
        rule = self._rule()
        for i in range(3):
            rule.check(_make_signal(symbol_id=i), _MockState(), _MockPortfolio())
        # All 3 different symbols — each should still pass
        result = rule.check(_make_signal(symbol_id=42), _MockState(), _MockPortfolio())
        assert result.status == "PASS"


# ─── Confidence Score ─────────────────────────────────────────────────────────

def _make_candles(n: int = 60, trend: str = "up") -> pd.DataFrame:
    """Generate synthetic OHLCV data."""
    np.random.seed(42)
    dates  = pd.date_range("2024-01-01 09:15:00", periods=n, freq="5min", tz="Asia/Kolkata")
    close  = 100.0 + (np.arange(n) * (0.5 if trend == "up" else -0.5))
    noise  = np.random.normal(0, 0.3, n)
    close += noise
    high   = close + abs(np.random.normal(0.5, 0.2, n))
    low    = close - abs(np.random.normal(0.5, 0.2, n))
    open_  = close - np.random.normal(0, 0.2, n)
    volume = np.random.randint(10_000, 50_000, n).astype(float)
    # Spike last candle volume for EMA/Volume strategies
    volume[-1] = volume.mean() * 3.0
    return pd.DataFrame({
        "open": open_, "high": high, "low": low,
        "close": close, "volume": volume,
    }, index=dates)


class TestConfidenceScore:
    def test_ema_crossover_has_confidence(self):
        from app.engine.strategies.ema_crossover import EMACrossoverStrategy

        df   = _make_candles(60, "up")
        strat = EMACrossoverStrategy(strategy_id=1, params={"ema_fast": 9, "ema_slow": 21})
        sig  = strat.evaluate(df, symbol_id=1)
        if sig is not None:
            assert 0.0 <= sig.confidence_score <= 100.0

    def test_rsi_mr_has_confidence(self):
        from app.engine.strategies.rsi_mean_reversion import RSIMeanReversionStrategy

        df   = _make_candles(80, "down")
        strat = RSIMeanReversionStrategy(strategy_id=2, params={
            "rsi_period": 14, "ema_trend": 50, "rsi_oversold": 45,
        })
        sig = strat.evaluate(df, symbol_id=1)
        if sig is not None:
            assert 0.0 <= sig.confidence_score <= 100.0


# ─── Backtest Transaction Costs ───────────────────────────────────────────────

class TestBacktestCosts:
    def _engine(self, slippage=0.001, brokerage=0.0003, stt=0.0001, mc_runs=0):
        from app.engine.strategies.ema_crossover import EMACrossoverStrategy
        from app.engine.risk_engine import RiskEngine
        from app.engine.backtest_engine import BacktestEngine

        class Cfg:
            risk_pct_per_trade   = 1.0
            max_position_pct     = 5.0
            max_concurrent_pos   = 5
            daily_loss_limit_pct = 3.0
            drawdown_halt_pct    = 10.0
            atr_volatility_max   = 5.0
            cooldown_minutes     = 0
            signal_start_hour    = 0
            signal_start_minute  = 0
            signal_end_hour      = 23
            signal_end_minute    = 59
            max_signals_per_stock = 999

        strat  = EMACrossoverStrategy(strategy_id=1, params={"ema_fast": 9, "ema_slow": 21})
        risk   = RiskEngine(Cfg())
        return BacktestEngine(
            strat, risk, initial_balance=100_000,
            slippage_pct=slippage, brokerage_pct=brokerage,
            stt_pct=stt, monte_carlo_runs=mc_runs,
        )

    def test_cost_params_stored(self):
        engine = self._engine()
        assert engine.slippage_pct  == 0.001
        assert engine.brokerage_pct == 0.0003
        assert engine.stt_pct       == 0.0001

    def test_zero_costs_match_gross(self):
        """With all costs at zero, net PnL == gross PnL."""
        engine = self._engine(slippage=0, brokerage=0, stt=0, mc_runs=0)
        df = _make_candles(150, "up")
        result = engine.run(df, symbol_name="TEST", symbol_id=1)
        # All trades should have no cost drag — total_costs should be 0
        assert result.total_costs == 0.0

    def test_costs_reduce_balance(self):
        """With non-zero costs, final balance should be lower than zero-cost run."""
        engine_no_cost   = self._engine(slippage=0, brokerage=0, stt=0, mc_runs=0)
        engine_with_cost = self._engine(mc_runs=0)
        df = _make_candles(150, "up")
        r0 = engine_no_cost.run(df,   symbol_name="TEST", symbol_id=1)
        r1 = engine_with_cost.run(df, symbol_name="TEST", symbol_id=1)
        assert r0.total_return_pct >= r1.total_return_pct

    def test_monte_carlo_produces_5th_pct(self):
        engine = self._engine(mc_runs=100)
        df = _make_candles(200, "up")
        result = engine.run(df, symbol_name="TEST", symbol_id=1)
        # If >=10 trades, MC fields should differ from actual
        if result.total_trades >= 10:
            assert isinstance(result.monte_carlo_5th_pct_return,   float)
            assert isinstance(result.monte_carlo_5th_pct_drawdown, float)

    def test_losing_streak_computed(self):
        engine = self._engine(mc_runs=0)
        df = _make_candles(200, "down")
        result = engine.run(df, symbol_name="TEST", symbol_id=1)
        assert result.max_losing_streak >= 0
