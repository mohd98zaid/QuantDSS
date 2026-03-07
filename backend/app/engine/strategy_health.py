"""
StrategyHealthMonitor — Tracks per-strategy performance and auto-disables
underperforming strategies.

Hardening Fixes (Issue 9):
  - DB persistence: trade outcomes written to strategy_health_log table on every
    record_trade() call. On startup, hydrate_from_db() replays the last 30 trades
    per strategy to rebuild the in-memory deque.
  - Pipeline integration: is_disabled() is now called by SignalPipeline before
    evaluating strategies. The singleton strategy_health_monitor is imported and
    checked in signal_pipeline.py.

Auto-disable thresholds (unchanged):
  - win_rate < 35% over last 20 trades → DISABLED (permanent until re-enable)
  - profit_factor < 1.0 over last 30 trades → DISABLED
  - consecutive_losses >= 5 → PAUSED for 24h (auto-re-enables)
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Deque, Optional

from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import logger

IST = timezone(timedelta(hours=5, minutes=30))

# ── Thresholds ────────────────────────────────────────────────────────────────
WIN_RATE_LOOKBACK      = 20
WIN_RATE_MIN_PCT       = 35.0
PF_LOOKBACK            = 30
PF_MIN                 = 1.0
MAX_CONSECUTIVE_LOSSES = 5


@dataclass
class StrategyMetrics:
    """Snapshot metrics for a single strategy."""
    strategy_id:         int
    win_rate_pct:        float = 0.0
    profit_factor:       float = 0.0
    avg_win:             float = 0.0
    avg_loss:            float = 0.0
    consecutive_losses:  int   = 0
    total_trades:        int   = 0
    is_disabled:         bool  = False
    disable_reason:      Optional[str] = None
    paused_until:        Optional[datetime] = None


@dataclass
class _StrategyState:
    """Internal rolling state for a strategy."""
    trades: Deque[float] = field(default_factory=lambda: deque(maxlen=PF_LOOKBACK))
    consecutive_losses: int = 0
    is_disabled: bool = False
    disable_reason: Optional[str] = None
    paused_until: Optional[datetime] = None


class StrategyHealthMonitor:
    """
    In-memory strategy health tracker with DB persistence.

    Usage (in signal pipeline):
        if strategy_health_monitor.is_disabled(strategy_id):
            continue  # skip this strategy

    Usage (on trade close):
        await strategy_health_monitor.record_trade_async(strategy_id, pnl, db)

    Startup (in main.py lifespan):
        await strategy_health_monitor.hydrate_from_db(db)
    """

    def __init__(self):
        self._states: dict[int, _StrategyState] = defaultdict(_StrategyState)

    # ── Public API ─────────────────────────────────────────────────────────────

    def record_trade(self, strategy_id: int, pnl: float) -> None:
        """
        Record a completed trade P&L for a strategy (in-memory only).
        Use record_trade_async() when a DB session is available.
        """
        state = self._states[strategy_id]
        state.trades.append(pnl)

        if pnl < 0:
            state.consecutive_losses += 1
        else:
            state.consecutive_losses = 0

        disabled, reason = self._should_disable(strategy_id)
        if disabled and not state.is_disabled:
            state.is_disabled = True
            state.disable_reason = reason

            if "CONSECUTIVE" in reason:
                state.paused_until = datetime.now(IST) + timedelta(hours=24)
                logger.warning(
                    f"StrategyHealth: strategy {strategy_id} PAUSED 24h ({reason})"
                )
            else:
                logger.warning(
                    f"StrategyHealth: strategy {strategy_id} DISABLED ({reason})"
                )

    async def record_trade_async(
        self, strategy_id: int, pnl: float, db: AsyncSession
    ) -> None:
        """
        Issue 9 Fix: Record a trade outcome and persist to DB.

        Writes to strategy_health_log table so that on the next server restart,
        hydrate_from_db() can replay the rolling window and restore health state.
        """
        # 1. Update in-memory state
        self.record_trade(strategy_id, pnl)

        # 2. Persist to DB
        try:
            from app.models.strategy_health_log import StrategyHealthLog
            log_entry = StrategyHealthLog(strategy_id=strategy_id, pnl=pnl)
            db.add(log_entry)
            await db.commit()
        except Exception as e:
            logger.error(f"StrategyHealth: Failed to persist trade log for strategy {strategy_id}: {e}")
            await db.rollback()

    async def hydrate_from_db(self, db: AsyncSession) -> None:
        """
        Issue 9 Fix: Replay the last PF_LOOKBACK trade outcomes per strategy
        from the DB to rebuild the in-memory deque on startup.

        Call this once in the application lifespan startup handler.
        """
        try:
            from app.models.strategy_health_log import StrategyHealthLog
            from sqlalchemy import text

            # Get all strategy IDs that have health logs
            result = await db.execute(
                select(StrategyHealthLog.strategy_id).distinct()
            )
            strategy_ids = [row[0] for row in result.fetchall()]

            for sid in strategy_ids:
                # Fetch last PF_LOOKBACK trades (newest first)
                result = await db.execute(
                    select(StrategyHealthLog)
                    .where(StrategyHealthLog.strategy_id == sid)
                    .order_by(desc(StrategyHealthLog.recorded_at))
                    .limit(PF_LOOKBACK)
                )
                rows = result.scalars().all()

                # Replay in chronological order (oldest first)
                for row in reversed(rows):
                    self.record_trade(sid, float(row.pnl))

            logger.info(
                f"StrategyHealth: hydrated {len(strategy_ids)} strategies from DB"
            )

        except Exception as e:
            logger.warning(f"StrategyHealth: hydrate_from_db failed (non-fatal): {e}")

    def is_disabled(self, strategy_id: int) -> bool:
        """Return True if this strategy should NOT generate signals right now."""
        state = self._states[strategy_id]
        if not state.is_disabled:
            return False

        # Check if a temporary pause has expired
        if state.paused_until and datetime.now(IST) > state.paused_until:
            state.is_disabled    = False
            state.disable_reason = None
            state.paused_until   = None
            logger.info(f"StrategyHealth: strategy {strategy_id} pause expired — re-enabled")
            return False

        return True

    def get_metrics(self, strategy_id: int) -> StrategyMetrics:
        """Return the current health snapshot for a strategy."""
        state = self._states[strategy_id]
        trades = list(state.trades)

        wr_trades = trades[-WIN_RATE_LOOKBACK:]
        winners   = [t for t in wr_trades if t > 0]
        losers    = [t for t in wr_trades if t < 0]
        win_rate  = (len(winners) / len(wr_trades) * 100) if wr_trades else 0.0

        pf_trades    = trades[-PF_LOOKBACK:]
        gross_profit = sum(t for t in pf_trades if t > 0)
        gross_loss   = abs(sum(t for t in pf_trades if t < 0))
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else 999.0

        avg_win  = (sum(winners) / len(winners)) if winners else 0.0
        avg_loss = (sum(losers)  / len(losers))  if losers  else 0.0

        return StrategyMetrics(
            strategy_id=strategy_id,
            win_rate_pct=round(win_rate, 1),
            profit_factor=round(profit_factor, 2),
            avg_win=round(avg_win, 2),
            avg_loss=round(avg_loss, 2),
            consecutive_losses=state.consecutive_losses,
            total_trades=len(trades),
            is_disabled=state.is_disabled,
            disable_reason=state.disable_reason,
            paused_until=state.paused_until,
        )

    def get_all_metrics(self) -> list[StrategyMetrics]:
        """Return health snapshots for all tracked strategies."""
        return [self.get_metrics(sid) for sid in self._states]

    def re_enable(self, strategy_id: int) -> None:
        """Manually re-enable a disabled strategy (e.g., after review)."""
        state = self._states[strategy_id]
        state.is_disabled    = False
        state.disable_reason = None
        state.paused_until   = None
        logger.info(f"StrategyHealth: strategy {strategy_id} manually re-enabled")

    # ── Internal ───────────────────────────────────────────────────────────────

    def _should_disable(self, strategy_id: int) -> tuple[bool, str]:
        """Evaluate disable conditions. Returns (should_disable, reason)."""
        state  = self._states[strategy_id]
        trades = list(state.trades)

        # Rule 1: consecutive loss pause
        if state.consecutive_losses >= MAX_CONSECUTIVE_LOSSES:
            return True, f"CONSECUTIVE_LOSSES_{state.consecutive_losses}"

        # Rule 2: win rate crash (only when we have enough data)
        wr_trades = trades[-WIN_RATE_LOOKBACK:]
        if len(wr_trades) >= WIN_RATE_LOOKBACK:
            winners  = [t for t in wr_trades if t > 0]
            win_rate = len(winners) / len(wr_trades) * 100
            if win_rate < WIN_RATE_MIN_PCT:
                return True, f"WIN_RATE_TOO_LOW ({win_rate:.1f}% < {WIN_RATE_MIN_PCT}%)"

        # Rule 3: profit factor collapse
        pf_trades = trades[-PF_LOOKBACK:]
        if len(pf_trades) >= PF_LOOKBACK:
            gross_profit = sum(t for t in pf_trades if t > 0)
            gross_loss   = abs(sum(t for t in pf_trades if t < 0))
            if gross_loss > 0:
                pf = gross_profit / gross_loss
                if pf < PF_MIN:
                    return True, f"PROFIT_FACTOR_TOO_LOW ({pf:.2f} < {PF_MIN})"

        return False, ""


# ── Singleton instance used by the pipeline ───────────────────────────────────
strategy_health_monitor = StrategyHealthMonitor()
