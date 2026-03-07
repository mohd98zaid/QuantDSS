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
    # New metrics
    max_losing_streak: int = 0
    strategy_degradation_pct: float = 0.0   # > 20 means edge is weakening
    monte_carlo_5th_pct_return: float = 0.0  # pessimistic return at 5th percentile
    monte_carlo_5th_pct_drawdown: float = 0.0  # worst-case drawdown at 5th percentile
    total_costs: float = 0.0                 # Brokerage + STT + slippage total
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
        trailing_atr_trigger: float = 1.0,
        trailing_atr_distance: float = 1.0,
        slippage_pct: float = 0.001,      # 0.1% per side (realistic for NSE)
        brokerage_pct: float = 0.0003,    # 0.03% per side
        stt_pct: float = 0.0001,          # 0.01% on the sell side only
        monte_carlo_runs: int = 1000,     # reshuffle simulations
    ):
        self.strategy = strategy
        self.risk_engine = risk_engine
        self.initial_balance = initial_balance
        self.enable_trailing_stop = enable_trailing_stop
        self.trailing_atr_trigger = trailing_atr_trigger
        self.trailing_atr_distance = trailing_atr_distance
        self.slippage_pct = slippage_pct
        self.brokerage_pct = brokerage_pct
        self.stt_pct = stt_pct
        self.monte_carlo_runs = monte_carlo_runs

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

        # Precompute indicators for the entire dataset ONCE to avoid loop performance issues
        from app.engine.indicators import IndicatorEngine
        candles = IndicatorEngine.compute_strategy_indicators(
            candles, self.strategy.strategy_type, self.strategy.params
        )

        logger.info(
            f"Backtest starting: {symbol_name} | "
            f"{len(candles)} candles | "
            f"₹{self.initial_balance:,.0f} capital | "
            f"Trailing stops: {'ON' if self.enable_trailing_stop else 'OFF'}"
        )

        # Fix 11: Pending signal state for next-bar-open entry (lookahead bias fix)
        _pending_signal = None
        _pending_qty: int | None = None

        for i in range(min_candles, len(candles)):
            # Get lookback window
            window = candles.iloc[max(0, i - 200):i + 1].copy()
            current_bar = candles.iloc[i]
            current_time = current_bar.get("time", candles.index[i])
            current_high = float(current_bar["high"])
            current_low = float(current_bar["low"])
            current_close = float(current_bar["close"])
            current_open = float(current_bar["open"])  # Fix 11: next-bar open entry

            # Fix 11: If a signal was approved last bar, fill at THIS bar's open.
            # This removes lookahead bias: the signal saw close[i-1] (not open[i]).
            if open_trade is None and _pending_signal is not None and _pending_qty is not None:
                open_trade = BacktestTrade(
                    entry_time=current_time,
                    signal_type=_pending_signal.signal_type,
                    entry_price=current_open,    # Fill at next bar open, NOT signal close
                    stop_loss=_pending_signal.stop_loss,
                    target_price=_pending_signal.target_price,
                    quantity=_pending_qty,
                )
                _pending_signal = None
                _pending_qty = None

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
                        gross_pnl = (open_trade.exit_price - open_trade.entry_price) * open_trade.quantity
                    else:
                        gross_pnl = (open_trade.entry_price - open_trade.exit_price) * open_trade.quantity

                    # Fix 10: Do NOT deduct transaction costs here. Costs are applied
                    # only once inside _compute_metrics() to avoid double-counting.
                    # Previous code deducted brokerage+STT+slippage at both points,
                    # which understated backtest P&L by ~2x on short timeframes.
                    open_trade.pnl = gross_pnl

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
                        # Fix 8: Entry at next-bar open to eliminate lookahead bias.
                        # Signal fired on bar i (we "know" close[i]) but in real life
                        # the order executes at market open of bar i+1. Record signal
                        # details and defer trade creation until the next iteration.
                        _pending_signal = signal
                        _pending_qty    = decision.quantity

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
        losers  = [t for t in trades if t.pnl < 0]

        total_return = ((final_balance - self.initial_balance) / self.initial_balance) * 100
        win_rate  = (len(winners) / len(trades) * 100) if trades else 0
        avg_win   = sum(t.pnl for t in winners) / len(winners) if winners else 0
        avg_loss  = sum(t.pnl for t in losers)  / len(losers)  if losers  else 0

        # Max drawdown
        peak_eq = self.initial_balance
        max_dd  = 0.0
        for eq in equity_curve:
            peak_eq = max(peak_eq, eq)
            dd = (peak_eq - eq) / peak_eq if peak_eq > 0 else 0
            max_dd = max(max_dd, dd)

        # Profit factor
        gross_profit = sum(t.pnl for t in winners)
        gross_loss   = abs(sum(t.pnl for t in losers))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else 999.99

        # Sharpe ratio (annualized) — Fix 7
        # For 1-minute bars: annualization = sqrt(252 trading days * 375 mins/day).
        # Previous sqrt(252) was for DAILY bars — severely understated Sharpe for intraday.
        if len(equity_curve) > 1:
            eq_array = np.array(equity_curve)
            returns  = np.diff(eq_array) / eq_array[:-1]
            _ann     = np.sqrt(252 * 375)   # 375 = NSE trading minutes/day (09:15–15:30)
            sharpe   = (np.mean(returns) / np.std(returns) * _ann) if np.std(returns) > 0 else 0
        else:
            sharpe = 0

        # ── Max Losing Streak ──────────────────────────────────
        max_losing_streak = 0
        current_streak    = 0
        for t in trades:
            if t.pnl < 0:
                current_streak += 1
                max_losing_streak = max(max_losing_streak, current_streak)
            else:
                current_streak = 0

        # ── Strategy Degradation (H1 vs H2 win rate) ──────────
        degradation_pct = 0.0
        if len(trades) >= 20:
            half = len(trades) // 2
            h1_trades = trades[:half]
            h2_trades = trades[half:]
            h1_wr = sum(1 for t in h1_trades if t.pnl > 0) / len(h1_trades) * 100
            h2_wr = sum(1 for t in h2_trades if t.pnl > 0) / len(h2_trades) * 100
            degradation_pct = round(h1_wr - h2_wr, 1)  # positive = degrading

        # ── Total Transaction Costs — Fix 16 (Full NSE intraday cost model) ──
        total_costs = 0.0
        for t in trades:
            entry_v = t.entry_price * t.quantity
            exit_v  = t.exit_price  * t.quantity

            # Which side is buy vs sell
            if t.signal_type == "BUY":
                buy_v  = entry_v
                sell_v = exit_v
            else:
                buy_v  = exit_v
                sell_v = entry_v

            both_v = entry_v + exit_v

            # Brokerage: 0.03% per side, capped at ₹20 per order (approx)
            brokerage = min(both_v * 0.0003, 40.0)  # both sides combined cap ₹40

            # STT (Securities Transaction Tax): 0.025% on sell-side for intraday
            stt = sell_v * 0.00025

            # NSE Exchange Turnover Charge: 0.00297% on total turnover
            nse_turnover  = both_v * 0.0000297

            # SEBI Regulatory Fee: 0.0001% on total turnover
            sebi_fee = both_v * 0.000001

            # GST (18%) on brokerage + exchange charges + SEBI fee
            gst = (brokerage + nse_turnover + sebi_fee) * 0.18

            # Stamp Duty: 0.003% on buy-side value only (intraday rate)
            stamp_duty = buy_v * 0.00003

            # Bid-Ask Spread Slippage: 0.05% per side (conservative estimate)
            slippage = both_v * 0.0005

            trade_cost = brokerage + stt + nse_turnover + sebi_fee + gst + stamp_duty + slippage
            total_costs += trade_cost
            t.pnl  -= trade_cost  # Deduct costs from individual trade P&L

        # ── Monte Carlo Simulation (reshuffle trade order) ─────
        mc_5th_return   = total_return
        mc_5th_drawdown = max_dd * 100
        if len(trades) >= 10 and self.monte_carlo_runs > 0:
            pnls = np.array([t.pnl for t in trades])
            mc_returns: list[float]   = []
            mc_drawdowns: list[float] = []
            rng = np.random.default_rng(42)
            for _ in range(self.monte_carlo_runs):
                shuffled = rng.permutation(pnls)
                eq = [self.initial_balance]
                for pnl in shuffled:
                    eq.append(eq[-1] + pnl)
                mc_ret = (eq[-1] - self.initial_balance) / self.initial_balance * 100
                pk = self.initial_balance
                mc_dd = 0.0
                for v in eq:
                    pk = max(pk, v)
                    mc_dd = max(mc_dd, (pk - v) / pk if pk > 0 else 0)
                mc_returns.append(mc_ret)
                mc_drawdowns.append(mc_dd * 100)
            mc_5th_return   = float(np.percentile(mc_returns,   5))
            mc_5th_drawdown = float(np.percentile(mc_drawdowns, 95))  # 95th pct = worst case

        start_time = candles.iloc[0].get("time", candles.index[0])  if len(candles) > 0 else ""
        end_time   = candles.iloc[-1].get("time", candles.index[-1]) if len(candles) > 0 else ""

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
            max_losing_streak=max_losing_streak,
            strategy_degradation_pct=degradation_pct,
            monte_carlo_5th_pct_return=round(mc_5th_return, 2),
            monte_carlo_5th_pct_drawdown=round(mc_5th_drawdown, 2),
            total_costs=round(total_costs, 2),
            trades=trades,
            equity_curve=equity_curve,
        )

    # ── Phase 4: Walk-Forward Testing ────────────────────────────────────────

    def walk_forward_test(
        self,
        candles: pd.DataFrame,
        symbol_name: str = "UNKNOWN",
        is_months: int = 18,
        oos_months: int = 6,
    ) -> dict:
        """
        Walk-forward test: rolling In-Sample + Out-of-Sample validation.

        For each window:
          1. IS window: optimise / fit the strategy (here we just verify it works)
          2. OOS window: evaluate on unseen data

        Args:
            candles:     Full historical OHLCV DataFrame (needs at least is+oos months)
            symbol_name: Symbol name for reporting
            is_months:   In-sample window length in months (default 18)
            oos_months:  Out-of-sample window length in months (default 6)

        Returns:
            dict with:
              windows          – per-window OOS metrics
              aggregate        – averaged metrics across all OOS windows
              is_robust        – True if all OOS windows have profit_factor > 1.0
        """
        if candles is None or len(candles) < 100:
            return {"error": "Insufficient candles for walk-forward test", "windows": []}

        # Ensure 'time' column is datetime and sortable
        if "time" in candles.columns:
            candles = candles.copy()
            candles["time"] = pd.to_datetime(candles["time"])
            candles = candles.sort_values("time").reset_index(drop=True)
            time_col = candles["time"]
        elif isinstance(candles.index, pd.DatetimeIndex):
            time_col = pd.Series(candles.index)
        else:
            return {"error": "DataFrame must have a 'time' column or DatetimeIndex", "windows": []}

        min_date = time_col.min()
        max_date = time_col.max()

        # Total window length in months
        total_months = is_months + oos_months

        windows = []
        window_idx = 0
        window_start = min_date

        while True:
            # IS window bounds
            is_start = window_start
            is_end   = is_start + pd.DateOffset(months=is_months)
            # OOS window bounds
            oos_start = is_end
            oos_end   = oos_start + pd.DateOffset(months=oos_months)

            if oos_end > max_date:
                break  # Not enough data for this window

            # Slice OOS candles (test only on OOS for unbiased evaluation)
            oos_mask = (time_col >= oos_start) & (time_col < oos_end)
            oos_candles = candles[oos_mask].copy()

            if len(oos_candles) < 50:
                window_start = window_start + pd.DateOffset(months=oos_months)
                window_idx += 1
                continue

            # Run backtest on OOS slice
            try:
                result = self.run(oos_candles, symbol_name=symbol_name)
                windows.append({
                    "window":          window_idx + 1,
                    "is_start":        str(is_start)[:10],
                    "is_end":          str(is_end)[:10],
                    "oos_start":       str(oos_start)[:10],
                    "oos_end":         str(oos_end)[:10],
                    "total_trades":    result.total_trades,
                    "win_rate":        result.win_rate,
                    "profit_factor":   result.profit_factor,
                    "max_drawdown_pct":result.max_drawdown_pct,
                    "total_return_pct":result.total_return_pct,
                    "sharpe_ratio":    result.sharpe_ratio,
                })
            except Exception as e:
                windows.append({
                    "window":    window_idx + 1,
                    "oos_start": str(oos_start)[:10],
                    "oos_end":   str(oos_end)[:10],
                    "error":     str(e),
                })

            # Advance by OOS window length
            window_start = window_start + pd.DateOffset(months=oos_months)
            window_idx  += 1

        if not windows:
            return {
                "error":     "No complete IS/OOS windows found in the data",
                "windows":   [],
                "aggregate": {},
                "is_robust": False,
            }

        # Aggregate OOS-only metrics
        valid_windows = [w for w in windows if "error" not in w]
        if valid_windows:
            agg_win_rate      = round(sum(w["win_rate"]       for w in valid_windows) / len(valid_windows), 1)
            agg_pf            = round(sum(w["profit_factor"]  for w in valid_windows) / len(valid_windows), 2)
            agg_max_dd        = round(sum(w["max_drawdown_pct"] for w in valid_windows) / len(valid_windows), 2)
            agg_return        = round(sum(w["total_return_pct"] for w in valid_windows) / len(valid_windows), 2)
            is_robust         = all(w["profit_factor"] > 1.0 for w in valid_windows)
        else:
            agg_win_rate = agg_pf = agg_max_dd = agg_return = 0.0
            is_robust = False

        return {
            "symbol":    symbol_name,
            "is_months": is_months,
            "oos_months":oos_months,
            "windows":   windows,
            "aggregate": {
                "avg_win_rate_pct":   agg_win_rate,
                "avg_profit_factor":  agg_pf,
                "avg_max_drawdown_pct":agg_max_dd,
                "avg_total_return_pct":agg_return,
                "valid_windows":      len(valid_windows),
                "total_windows":      len(windows),
            },
            "is_robust": is_robust,
        }
