"""
ResearchStrategyRunner — Batch backtesting wrapper around existing engines.

Reuses the existing BacktestEngine and Strategy classes.
No logic is duplicated — this module provides a higher-level API
for running multiple strategies across multiple symbols.

Usage:
    runner = ResearchStrategyRunner()
    results = await runner.run_batch(
        symbols=["RELIANCE", "TCS"],
        start_date="2025-01-01",
        end_date="2025-12-31",
        strategy_names=["ema_crossover", "rsi_mean_reversion"],
    )
"""
from __future__ import annotations

from datetime import date
from typing import Optional

import pandas as pd

from app.core.logging import logger
from app.engine.backtest_engine import BacktestEngine, BacktestResult
from app.engine.risk_engine import RiskEngine


class ResearchStrategyRunner:
    """
    High-level batch backtesting runner.

    Loads strategies from DB or by name, runs BacktestEngine against
    historical data loaded via DataLoader, and collects results.
    """

    def __init__(
        self,
        initial_balance: float = 100_000.0,
        enable_trailing_stop: bool = True,
        slippage_pct: float = 0.001,
    ):
        self.initial_balance = initial_balance
        self.enable_trailing_stop = enable_trailing_stop
        self.slippage_pct = slippage_pct

    async def run_single(
        self,
        candles: pd.DataFrame,
        strategy_name: str,
        symbol_name: str = "TEST",
        risk_config=None,
    ) -> Optional[BacktestResult]:
        """
        Run a single strategy backtest on provided candle data.

        Uses existing BacktestEngine from engine/backtest_engine.py.
        """
        try:
            strategy = self._load_strategy(strategy_name)
            if strategy is None:
                logger.warning(f"ResearchRunner: Unknown strategy '{strategy_name}'")
                return None

            if risk_config is None:
                from app.core.config import settings
                risk_config = type("RC", (), {
                    "risk_per_trade_pct": settings.risk_per_trade_pct,
                    "max_daily_loss_inr": settings.max_daily_loss_inr,
                    "max_daily_loss_pct": settings.max_daily_loss_pct,
                    "max_account_drawdown_pct": settings.max_account_drawdown_pct,
                    "cooldown_minutes": settings.cooldown_minutes,
                    "min_atr_pct": settings.min_atr_pct,
                    "max_atr_pct": settings.max_atr_pct,
                    "max_position_pct": settings.max_position_pct,
                    "max_concurrent_positions": settings.max_concurrent_positions,
                    "signal_start_hour": 0, "signal_start_minute": 0,
                    "signal_end_hour": 23, "signal_end_minute": 59,
                    "max_signals_per_stock": 999,
                })()

            risk_engine = RiskEngine(risk_config)
            bt = BacktestEngine(
                strategy=strategy,
                risk_engine=risk_engine,
                initial_balance=self.initial_balance,
                enable_trailing_stop=self.enable_trailing_stop,
                slippage_pct=self.slippage_pct,
            )

            result = bt.run(candles, symbol_name=symbol_name)
            logger.info(
                f"ResearchRunner: {strategy_name} on {symbol_name} — "
                f"Return={result.total_return_pct}%, "
                f"Sharpe={result.sharpe_ratio}, "
                f"Trades={result.total_trades}"
            )
            return result

        except Exception as e:
            logger.exception(f"ResearchRunner: Backtest failed — {e}")
            return None

    async def run_batch(
        self,
        symbols: list[str],
        start_date: str,
        end_date: str,
        strategy_names: list[str],
    ) -> list[dict]:
        """
        Run backtests for multiple strategies across multiple symbols.

        Returns a list of result summaries.
        """
        from app.research.backtest_engine.data_loader import DataLoader

        results = []
        for symbol in symbols:
            candles = await DataLoader.load_candles(symbol, start_date, end_date)
            if candles.empty:
                continue
            for strat in strategy_names:
                result = await self.run_single(candles, strat, symbol_name=symbol)
                if result:
                    results.append({
                        "symbol": result.symbol,
                        "strategy": result.strategy_name,
                        "total_return_pct": result.total_return_pct,
                        "sharpe_ratio": result.sharpe_ratio,
                        "max_drawdown_pct": result.max_drawdown_pct,
                        "win_rate": result.win_rate,
                        "profit_factor": result.profit_factor,
                        "total_trades": result.total_trades,
                    })
        return results

    @staticmethod
    def _load_strategy(name: str):
        """Load a strategy class by name."""
        from app.engine.strategies import STRATEGY_MAP
        strategy_cls = STRATEGY_MAP.get(name)
        if strategy_cls:
            return strategy_cls()
        return None
