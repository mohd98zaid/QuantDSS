"""
PerformanceMetrics — Compute trading performance metrics.

Wraps and extends the metrics already computed by BacktestEngine.
Adds expectancy and other research-specific metrics.
"""
from __future__ import annotations

from dataclasses import dataclass
import numpy as np


@dataclass
class MetricsSummary:
    """Extended performance metrics for research."""
    sharpe_ratio: float = 0.0
    max_drawdown_pct: float = 0.0
    profit_factor: float = 0.0
    win_rate: float = 0.0
    expectancy: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    total_trades: int = 0
    total_return_pct: float = 0.0
    calmar_ratio: float = 0.0


class PerformanceMetrics:
    """Compute performance metrics from a list of trade P&Ls."""

    @staticmethod
    def compute(pnls: list[float], initial_balance: float = 100_000.0) -> MetricsSummary:
        """
        Compute all performance metrics from individual trade P&Ls.

        Args:
            pnls: List of per-trade P&L values
            initial_balance: Starting capital

        Returns:
            MetricsSummary with all computed metrics
        """
        if not pnls:
            return MetricsSummary()

        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]

        total_return = sum(pnls)
        total_return_pct = (total_return / initial_balance) * 100

        win_rate = (len(wins) / len(pnls) * 100) if pnls else 0
        avg_win = np.mean(wins) if wins else 0
        avg_loss = np.mean(losses) if losses else 0

        # Profit factor
        gross_profit = sum(wins) if wins else 0
        gross_loss = abs(sum(losses)) if losses else 0
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else 999.99

        # Expectancy
        expectancy = float(np.mean(pnls)) if pnls else 0

        # Equity curve for Sharpe and drawdown
        equity = [initial_balance]
        for pnl in pnls:
            equity.append(equity[-1] + pnl)

        # Max drawdown
        peak = initial_balance
        max_dd = 0.0
        for eq in equity:
            peak = max(peak, eq)
            dd = (peak - eq) / peak if peak > 0 else 0
            max_dd = max(max_dd, dd)

        # Sharpe ratio (annualized, assuming ~252 trading days)
        if len(equity) > 1:
            returns = np.diff(equity) / np.array(equity[:-1])
            sharpe = (np.mean(returns) / np.std(returns) * np.sqrt(252)) if np.std(returns) > 0 else 0
        else:
            sharpe = 0

        # Calmar ratio
        calmar = (total_return_pct / (max_dd * 100)) if max_dd > 0 else 0

        return MetricsSummary(
            sharpe_ratio=round(float(sharpe), 2),
            max_drawdown_pct=round(max_dd * 100, 2),
            profit_factor=round(profit_factor, 2),
            win_rate=round(win_rate, 1),
            expectancy=round(expectancy, 2),
            avg_win=round(float(avg_win), 2),
            avg_loss=round(float(avg_loss), 2),
            total_trades=len(pnls),
            total_return_pct=round(total_return_pct, 2),
            calmar_ratio=round(calmar, 2),
        )
