"""
BacktestEngine — Runs strategies against historical data with simulated risk management.
Uses the same Strategy Engine + Risk Engine as live, but with simulated state.

Features:
  - Trailing stop (moves SL to breakeven after 1 ATR in profit)
  - Persistent daily risk state across the run
  - Full metrics: Sharpe, Drawdown, Profit Factor, Equity Curve
"""
import math
from dataclasses import dataclass, field
from datetime import datetime, date

import numpy as np
import pandas as pd

from app.core.logging import logger
from app.engine.base_strategy import BaseStrategy, RawSignal
from app.engine.risk_engine import RiskEngine, RiskDecision, Portfolio


@dataclass
class SimulatedDailyRiskState:
    """In-memory daily risk state for backtesting — persists across the run."""
    realised_pnl: float = 0.0
    last_signal_time: datetime | None = None
    is_halted: bool = False
    halt_reason: str | None = None
    halt_triggered_at: datetime | None = None
    signals_approved: int = 0
    signals_blocked: int = 0
    signals_skipped: int = 0


@dataclass
class BacktestTrade:
    """A single trade in a backtest."""
    entry_time: datetime
    exit_time: datetime | None = None
    signal_type: str = "BUY"
    entry_price: float = 0.0
    exit_price: float = 0.0
    stop_loss: float = 0.0
    target_price: float = 0.0
    quantity: int = 0
    pnl: float = 0.0
    exit_reason: str = ""    # TARGET_HIT, STOP_HIT, TRAILING_STOP, FORCED_EXIT
    trailing_activated: bool = False


@dataclass
class BacktestResult:
    """Aggregate backtesting results."""
    strategy_name: str
    symbol: str
    start_date: str
    end_date: str
    initial_balance: float
    final_balance: float
    total_return_pct: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    avg_win: float
    avg_loss: float
    max_drawdown_pct: float
    sharpe_ratio: float
    profit_factor: float
    trades: list[BacktestTrade] = field(default_factory=list)
    equity_curve: list[float] = field(default_factory=list)


class BacktestEngine:
    """
    Backtesting engine using the same strategy + risk engine as live.

    Process:
    1. Iterate through candles chronologically
    2. On each bar, evaluate strategy with lookback window
    3. If signal generated, run through risk engine
    4. If approved, simulate trade execution
    5. Track trailing stops, P&L, drawdown, and generate metrics
    """

    def __init__(
        self,
        strategy: BaseStrategy,
        risk_engine: RiskEngine,
        initial_balance: float = 100_000.0,
        enable_trailing_stop: bool = True,
        trailing_atr_trigger: float = 1.0,  # Activate trailing after 1 ATR profit
        trailing_atr_distance: float = 1.0, # Trail SL at 1 ATR behind price
    ):
        self.strategy = strategy
        self.risk_engine = risk_engine
        self.initial_balance = initial_balance
        self.enable_trailing_stop = enable_trailing_stop
        self.trailing_atr_trigger = trailing_atr_trigger
        self.trailing_atr_distance = trailing_atr_distance

    def run(
        self,
        candles: pd.DataFrame,
        symbol_name: str = "TEST",
        symbol_id: int = 1,
    ) -> BacktestResult:
        """Run backtest over historical candle data."""
        balance = self.initial_balance
        peak_balance = self.initial_balance
        trades: list[BacktestTrade] = []
        equity_curve = [self.initial_balance]
        open_trade: BacktestTrade | None = None
        current_atr: float = 0.0

        # Persistent risk state across the entire run
        sim_state = SimulatedDailyRiskState()
        current_trade_date: date | None = None

        min_candles = self.strategy.min_candles_required

        logger.info(
            f"Backtest starting: {symbol_name} | "
            f"{len(candles)} candles | "
            f"₹{self.initial_balance:,.0f} capital | "
            f"Trailing stops: {'ON' if self.enable_trailing_stop else 'OFF'}"
        )

        for i in range(min_candles, len(candles)):
            # Get lookback window
            window = candles.iloc[max(0, i - 200):i + 1].copy()
            current_bar = candles.iloc[i]
            current_time = current_bar.get("time", candles.index[i])
            current_high = float(current_bar["high"])
            current_low = float(current_bar["low"])
            current_close = float(current_bar["close"])

            # Reset daily risk state at day boundaries
            bar_date = current_time.date() if hasattr(current_time, "date") else None
            if bar_date and bar_date != current_trade_date:
                sim_state.realised_pnl = 0.0
                sim_state.is_halted = False
                sim_state.halt_reason = None
                current_trade_date = bar_date

            # ─── Check open trade for SL/Target/Trailing hit ──
            if open_trade is not None:
                closed = False

                # ─── Trailing Stop Logic ──────────────────────
                if self.enable_trailing_stop and current_atr > 0:
                    if open_trade.signal_type == "BUY":
                        profit_distance = current_high - open_trade.entry_price
                        if profit_distance >= self.trailing_atr_trigger * current_atr:
                            # Activate trailing — move SL up
                            new_sl = current_high - (self.trailing_atr_distance * current_atr)
                            if new_sl > open_trade.stop_loss:
                                open_trade.stop_loss = new_sl
                                open_trade.trailing_activated = True
                    else:  # SELL
                        profit_distance = open_trade.entry_price - current_low
                        if profit_distance >= self.trailing_atr_trigger * current_atr:
                            new_sl = current_low + (self.trailing_atr_distance * current_atr)
                            if new_sl < open_trade.stop_loss:
                                open_trade.stop_loss = new_sl
                                open_trade.trailing_activated = True

                # ─── Check SL/Target Hit ──────────────────────
                if open_trade.signal_type == "BUY":
                    if current_low <= open_trade.stop_loss:
                        open_trade.exit_price = open_trade.stop_loss
                        open_trade.exit_reason = "TRAILING_STOP" if open_trade.trailing_activated else "STOP_HIT"
                        closed = True
                    elif current_high >= open_trade.target_price:
                        open_trade.exit_price = open_trade.target_price
                        open_trade.exit_reason = "TARGET_HIT"
                        closed = True
                else:  # SELL
                    if current_high >= open_trade.stop_loss:
                        open_trade.exit_price = open_trade.stop_loss
                        open_trade.exit_reason = "TRAILING_STOP" if open_trade.trailing_activated else "STOP_HIT"
                        closed = True
                    elif current_low <= open_trade.target_price:
                        open_trade.exit_price = open_trade.target_price
                        open_trade.exit_reason = "TARGET_HIT"
                        closed = True

                if closed:
                    open_trade.exit_time = current_time
                    if open_trade.signal_type == "BUY":
                        open_trade.pnl = (open_trade.exit_price - open_trade.entry_price) * open_trade.quantity
                    else:
                        open_trade.pnl = (open_trade.entry_price - open_trade.exit_price) * open_trade.quantity

                    balance += open_trade.pnl
                    peak_balance = max(peak_balance, balance)

                    # Update persistent risk state
                    sim_state.realised_pnl += open_trade.pnl

                    trades.append(open_trade)
                    open_trade = None

            # ─── Evaluate strategy (only if no open trade & not halted) ───
            if open_trade is None and not sim_state.is_halted:
                signal = self.strategy.evaluate(window, symbol_id)

                if signal is not None:
                    current_atr = signal.atr_value  # Save for trailing stop

                    portfolio = Portfolio(
                        current_balance=balance,
                        peak_balance=peak_balance,
                        open_positions=1 if open_trade else 0,
                    )

                    decision = self.risk_engine.validate(signal, sim_state, portfolio)

                    # Update sim state counters
                    if decision.status == "APPROVED":
                        sim_state.signals_approved += 1
                        sim_state.last_signal_time = current_time
                    elif decision.status == "BLOCKED":
                        sim_state.signals_blocked += 1
                        if decision.reason in ("DAILY_LOSS_LIMIT_REACHED", "ACCOUNT_DRAWDOWN_HALT"):
                            sim_state.is_halted = True
                            sim_state.halt_reason = decision.reason
                    elif decision.status == "SKIPPED":
                        sim_state.signals_skipped += 1

                    if decision.status == "APPROVED" and decision.quantity:
                        open_trade = BacktestTrade(
                            entry_time=current_time,
                            signal_type=signal.signal_type,
                            entry_price=signal.entry_price,
                            stop_loss=signal.stop_loss,
                            target_price=signal.target_price,
                            quantity=decision.quantity,
                        )

            equity_curve.append(balance)

        # Force close any open trade at last price
        if open_trade is not None:
            last_bar = candles.iloc[-1]
            open_trade.exit_price = float(last_bar["close"])
            open_trade.exit_time = last_bar.get("time", candles.index[-1])
            open_trade.exit_reason = "FORCED_EXIT"
            if open_trade.signal_type == "BUY":
                open_trade.pnl = (open_trade.exit_price - open_trade.entry_price) * open_trade.quantity
            else:
                open_trade.pnl = (open_trade.entry_price - open_trade.exit_price) * open_trade.quantity
            balance += open_trade.pnl
            trades.append(open_trade)

        return self._compute_metrics(
            trades, equity_curve, balance, symbol_name, candles
        )

    def _compute_metrics(
        self,
        trades: list[BacktestTrade],
        equity_curve: list[float],
        final_balance: float,
        symbol_name: str,
        candles: pd.DataFrame,
    ) -> BacktestResult:
        """Compute aggregate performance metrics from trades."""
        winners = [t for t in trades if t.pnl > 0]
        losers = [t for t in trades if t.pnl < 0]

        total_return = ((final_balance - self.initial_balance) / self.initial_balance) * 100

        win_rate = (len(winners) / len(trades) * 100) if trades else 0
        avg_win = sum(t.pnl for t in winners) / len(winners) if winners else 0
        avg_loss = sum(t.pnl for t in losers) / len(losers) if losers else 0

        # Max drawdown
        peak_eq = self.initial_balance
        max_dd = 0.0
        for eq in equity_curve:
            peak_eq = max(peak_eq, eq)
            dd = (peak_eq - eq) / peak_eq if peak_eq > 0 else 0
            max_dd = max(max_dd, dd)

        # Profit factor
        gross_profit = sum(t.pnl for t in winners)
        gross_loss = abs(sum(t.pnl for t in losers))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

        # Sharpe ratio (annualized from per-bar returns)
        if len(equity_curve) > 1:
            eq_array = np.array(equity_curve)
            returns = np.diff(eq_array) / eq_array[:-1]
            sharpe = (np.mean(returns) / np.std(returns) * np.sqrt(252)) if np.std(returns) > 0 else 0
        else:
            sharpe = 0

        start_time = candles.iloc[0].get("time", candles.index[0]) if len(candles) > 0 else ""
        end_time = candles.iloc[-1].get("time", candles.index[-1]) if len(candles) > 0 else ""

        return BacktestResult(
            strategy_name=self.strategy.__class__.__name__,
            symbol=symbol_name,
            start_date=str(start_time)[:10],
            end_date=str(end_time)[:10],
            initial_balance=self.initial_balance,
            final_balance=round(final_balance, 2),
            total_return_pct=round(total_return, 2),
            total_trades=len(trades),
            winning_trades=len(winners),
            losing_trades=len(losers),
            win_rate=round(win_rate, 1),
            avg_win=round(avg_win, 2),
            avg_loss=round(avg_loss, 2),
            max_drawdown_pct=round(max_dd * 100, 2),
            sharpe_ratio=round(float(sharpe), 2),
            profit_factor=round(profit_factor, 2),
            trades=trades,
            equity_curve=equity_curve,
        )
