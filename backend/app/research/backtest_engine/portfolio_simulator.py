"""
PortfolioSimulator — Multi-strategy portfolio simulation.

Simulates a portfolio running multiple strategies simultaneously
with position sizing and capital allocation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from app.core.logging import logger
from app.engine.backtest_engine import BacktestResult


@dataclass
class PortfolioResult:
    """Aggregated portfolio simulation result."""
    strategies: list[str] = field(default_factory=list)
    symbols: list[str] = field(default_factory=list)
    initial_balance: float = 100_000.0
    final_balance: float = 0.0
    total_return_pct: float = 0.0
    combined_trades: int = 0
    strategy_results: list[dict] = field(default_factory=list)


class PortfolioSimulator:
    """
    Simulates a portfolio with multiple strategies.

    Allocates capital equally across strategies and tracks
    aggregate performance.
    """

    def __init__(self, initial_balance: float = 100_000.0):
        self.initial_balance = initial_balance

    def simulate(
        self,
        results: list[BacktestResult],
        allocation: str = "equal",
    ) -> PortfolioResult:
        """
        Combine multiple backtest results into a portfolio view.

        Args:
            results: Individual strategy backtest results
            allocation: Capital allocation method ("equal")

        Returns:
            PortfolioResult with aggregated metrics
        """
        if not results:
            return PortfolioResult(initial_balance=self.initial_balance)

        n = len(results)
        per_strategy_capital = self.initial_balance / n

        total_pnl = 0.0
        strategy_summaries = []

        for r in results:
            # Scale P&L proportionally to allocation
            scale = per_strategy_capital / r.initial_balance if r.initial_balance > 0 else 1.0
            scaled_pnl = (r.final_balance - r.initial_balance) * scale
            total_pnl += scaled_pnl

            strategy_summaries.append({
                "strategy": r.strategy_name,
                "symbol": r.symbol,
                "return_pct": r.total_return_pct,
                "sharpe": r.sharpe_ratio,
                "trades": r.total_trades,
                "win_rate": r.win_rate,
                "profit_factor": r.profit_factor,
            })

        final_balance = self.initial_balance + total_pnl
        total_return = (total_pnl / self.initial_balance) * 100

        return PortfolioResult(
            strategies=list(set(r.strategy_name for r in results)),
            symbols=list(set(r.symbol for r in results)),
            initial_balance=self.initial_balance,
            final_balance=round(final_balance, 2),
            total_return_pct=round(total_return, 2),
            combined_trades=sum(r.total_trades for r in results),
            strategy_results=strategy_summaries,
        )
