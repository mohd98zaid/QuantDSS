"""RiskConfig model — System Risk Configuration (Singleton)"""
from sqlalchemy import Column, DateTime, Integer, Numeric
from sqlalchemy.sql import func

from app.core.database import Base


class RiskConfig(Base):
    __tablename__ = "risk_config"

    id = Column(Integer, primary_key=True, autoincrement=True)
    risk_per_trade_pct = Column(Numeric(6, 4), default=0.01)       # 1%
    max_daily_loss_inr = Column(Numeric(12, 2), default=500.00)
    max_daily_loss_pct = Column(Numeric(6, 4), default=0.02)       # 2%
    max_account_drawdown_pct = Column(Numeric(6, 4), default=0.10) # 10%
    cooldown_minutes = Column(Integer, default=15)
    min_atr_pct = Column(Numeric(6, 4), default=0.003)             # 0.3%
    max_atr_pct = Column(Numeric(6, 4), default=0.030)             # 3.0%
    max_position_pct = Column(Numeric(6, 4), default=0.20)         # 20%
    max_concurrent_positions = Column(Integer, default=2)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Singleton enforcement is done via unique index in migration

    def __repr__(self):
        return f"<RiskConfig risk={self.risk_per_trade_pct} daily_cap={self.max_daily_loss_inr}>"
