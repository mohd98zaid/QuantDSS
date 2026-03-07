"""StrategyHealth model — Per-strategy health metrics snapshot."""
from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.core.database import Base


class StrategyHealth(Base):
    __tablename__ = "strategy_health"

    id              = Column(Integer, primary_key=True, autoincrement=True)
    strategy_id     = Column(Integer, ForeignKey("strategies.id", ondelete="CASCADE"), nullable=False, unique=True)
    win_rate_pct    = Column(Float, nullable=True)         # over last 20 trades
    profit_factor   = Column(Float, nullable=True)         # over last 30 trades
    avg_win         = Column(Float, nullable=True)
    avg_loss        = Column(Float, nullable=True)
    consecutive_losses = Column(Integer, default=0)
    total_trades    = Column(Integer, default=0)
    is_disabled     = Column(Boolean, default=False)
    disable_reason  = Column(Text, nullable=True)
    paused_until    = Column(DateTime(timezone=True), nullable=True)
    last_evaluated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    strategy = relationship("Strategy")

    __table_args__ = (
        Index("idx_strategy_health_strategy", "strategy_id"),
    )

    def __repr__(self):
        return f"<StrategyHealth strategy={self.strategy_id} wr={self.win_rate_pct}% pf={self.profit_factor}>"
