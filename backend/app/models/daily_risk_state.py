"""DailyRiskState model — Daily Risk Tracker (One Row Per Trading Day)"""
from sqlalchemy import Boolean, Column, Date, DateTime, Integer, Numeric, Text
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

    def __repr__(self):
        return f"<DailyRiskState {self.trade_date} PnL={self.realised_pnl} halted={self.is_halted}>"
