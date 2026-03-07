"""DailyRiskState model — Daily Risk Tracker (One Row Per Trading Day)"""
from sqlalchemy import Boolean, Column, Date, DateTime, Integer, JSON, Numeric, Text
from sqlalchemy.sql import func

from app.core.database import Base


class DailyRiskState(Base):
    __tablename__ = "daily_risk_state"

    id = Column(Integer, primary_key=True, autoincrement=True)
    trade_date = Column(Date, unique=True, nullable=False)
    account_balance = Column(Numeric(14, 2))
    peak_balance = Column(Numeric(14, 2))
    max_daily_loss = Column(Numeric(12, 2))
    realised_pnl = Column(Numeric(12, 2), default=0)
    unrealised_pnl = Column(Numeric(12, 2), default=0)
    trades_taken = Column(Integer, default=0)
    signals_approved = Column(Integer, default=0)
    signals_blocked = Column(Integer, default=0)
    signals_skipped = Column(Integer, default=0)
    last_signal_time = Column(DateTime(timezone=True), nullable=True)
    is_halted = Column(Boolean, default=False)
    halt_reason = Column(Text, nullable=True)
    halt_triggered_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Issue 7 Fix: DB-persisted per-stock signal counter.
    # Stores {str(symbol_id): count} so MaxSignalsPerStockPerDay survives restarts.
    # The risk engine reads/writes this dict when approving signals.
    # Gets reset to {} automatically when a new DailyRiskState row is created
    # at the start of each trading day.
    signals_per_stock = Column(JSON, default=dict, nullable=False, server_default="{}")

    def __repr__(self):
        return f"<DailyRiskState {self.trade_date} PnL={self.realised_pnl} halted={self.is_halted}>"
