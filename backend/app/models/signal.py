"""Signal model — Generated Signals (Approved / Blocked / Skipped)"""
from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, Numeric, String, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.core.database import Base


class Signal(Base):
    __tablename__ = "signals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now())
    strategy_id = Column(Integer, ForeignKey("strategies.id"), nullable=True)
    symbol_id = Column(Integer, ForeignKey("symbols.id"), nullable=True)
    signal_type = Column(String(10), nullable=False)  # BUY / SELL / EXIT
    entry_price = Column(Numeric(12, 2))
    stop_loss = Column(Numeric(12, 2))
    target_price = Column(Numeric(12, 2))
    quantity = Column(Integer)
    risk_amount = Column(Numeric(12, 2))
    risk_pct = Column(Numeric(6, 4))
    risk_reward = Column(Numeric(6, 2))
    risk_status = Column(String(20), nullable=False)  # APPROVED / BLOCKED / SKIPPED
    block_reason = Column(Text, nullable=True)
    atr_value = Column(Numeric(12, 4))
    atr_pct = Column(Numeric(6, 4))
    candle_time = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    strategy = relationship("Strategy", back_populates="signals")
    symbol = relationship("Symbol", back_populates="signals")
    trades = relationship("Trade", back_populates="signal")

    __table_args__ = (
        Index("idx_signals_timestamp", "timestamp"),
        Index("idx_signals_symbol", "symbol_id", "timestamp"),
        Index("idx_signals_status", "risk_status", "timestamp"),
    )

    def __repr__(self):
        return f"<Signal {self.signal_type} {self.risk_status} @ {self.entry_price}>"
