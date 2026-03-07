"""
StrategyHealthLog — Persistent trade outcome log for StrategyHealthMonitor.

Issue 9 Fix: Replaces the pure in-memory deque-based health tracking with a
database-backed log. On server startup, the StrategyHealthMonitor replays the
last 30 recorded trades per strategy from this table to rebuild its deque state.

This ensures that:
  - Disabled strategies remain disabled after a server restart
  - Consecutive-loss counters are accurate across process restarts
  - Win rate and profit factor calculations are based on real historical trades

Table Design
------------
One row per closed trade that contributes to health tracking. Only strategy-level
P&L matters here (not individual fill details), so this is intentionally minimal.

Retention
---------
Old rows can be pruned after 90 days — the monitor only uses the last 30 rows
per strategy, and win_rate_pct snapshots are also stored in strategy_health.
"""
from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, Numeric
from sqlalchemy.sql import func

from app.core.database import Base


class StrategyHealthLog(Base):
    __tablename__ = "strategy_health_log"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    strategy_id = Column(
        Integer,
        ForeignKey("strategies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Net P&L of the closed trade (positive = win, negative = loss)
    pnl         = Column(Numeric(12, 2), nullable=False)
    recorded_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        # Fast lookup: latest N trades per strategy for hydration on startup
        Index("idx_shl_strategy_recorded", "strategy_id", "recorded_at"),
    )

    def __repr__(self):
        return f"<StrategyHealthLog strategy={self.strategy_id} pnl={self.pnl} at={self.recorded_at}>"
