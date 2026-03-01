"""Backtest models — BacktestRun + BacktestTrade"""
from sqlalchemy import Column, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.core.database import Base


class BacktestRun(Base):
    __tablename__ = "backtest_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    strategy_id = Column(Integer, ForeignKey("strategies.id"), nullable=True)
    symbol_id = Column(Integer, ForeignKey("symbols.id"), nullable=True)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    initial_capital = Column(Numeric(14, 2))
    slippage_pct = Column(Numeric(6, 4), default=0.0005)
    brokerage_per_order = Column(Numeric(10, 2), default=20.00)
    status = Column(String(20), default="PENDING")  # PENDING / RUNNING / COMPLETED / FAILED

    # Output metrics
    total_return_pct = Column(Numeric(10, 4))
    cagr_pct = Column(Numeric(10, 4))
    win_rate_pct = Column(Numeric(6, 4))
    profit_factor = Column(Numeric(8, 4))
    sharpe_ratio = Column(Numeric(8, 4))
    sortino_ratio = Column(Numeric(8, 4))
    max_drawdown_pct = Column(Numeric(6, 4))
    max_drawdown_days = Column(Integer)
    total_trades = Column(Integer)
    winning_trades = Column(Integer)
    losing_trades = Column(Integer)

    # Walk-forward
    insample_end_date = Column(Date, nullable=True)
    insample_return_pct = Column(Numeric(10, 4))
    oos_return_pct = Column(Numeric(10, 4))

    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    trades = relationship("BacktestTrade", back_populates="run", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<BacktestRun {self.id} {self.status} return={self.total_return_pct}%>"


class BacktestTrade(Base):
    __tablename__ = "backtest_trades"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(Integer, ForeignKey("backtest_runs.id", ondelete="CASCADE"), nullable=False)
    symbol_id = Column(Integer, ForeignKey("symbols.id"), nullable=True)
    entry_time = Column(DateTime(timezone=True))
    exit_time = Column(DateTime(timezone=True))
    entry_price = Column(Numeric(12, 2))
    exit_price = Column(Numeric(12, 2))
    quantity = Column(Integer)
    direction = Column(String(5))  # LONG / SHORT
    gross_pnl = Column(Numeric(12, 2))
    costs = Column(Numeric(10, 2))
    net_pnl = Column(Numeric(12, 2))
    exit_reason = Column(String(50))
    risk_status = Column(String(20))
    block_reason = Column(Text, nullable=True)

    # Relationships
    run = relationship("BacktestRun", back_populates="trades")

    __table_args__ = (
        Index("idx_bt_trades_run", "run_id"),
    )

    def __repr__(self):
        return f"<BacktestTrade run={self.run_id} PnL={self.net_pnl}>"
