"""Trade model — Trade Journal (Manual Outcome Entries)"""
from sqlalchemy import Boolean, Column, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.core.database import Base


class Trade(Base):
    __tablename__ = "trades"

    id = Column(Integer, primary_key=True, autoincrement=True)
    signal_id = Column(Integer, ForeignKey("signals.id"), nullable=True)
    symbol_id = Column(Integer, ForeignKey("symbols.id"), nullable=False)
    trade_date = Column(Date, nullable=False)
    entry_time = Column(DateTime(timezone=True))
    exit_time = Column(DateTime(timezone=True))
    entry_price = Column(Numeric(12, 2))
    exit_price = Column(Numeric(12, 2))
    quantity = Column(Integer)
    direction = Column(String(5), nullable=False)  # LONG / SHORT
    gross_pnl = Column(Numeric(12, 2))
    brokerage = Column(Numeric(10, 2))
    net_pnl = Column(Numeric(12, 2))
    exit_reason = Column(String(50))  # SL_HIT / TARGET_HIT / MANUAL / EOD
    notes = Column(Text)
    is_deleted = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    signal = relationship("Signal", back_populates="trades")
    symbol = relationship("Symbol", back_populates="trades")

    __table_args__ = (
        Index("idx_trades_date", "trade_date"),
        Index("idx_trades_symbol", "symbol_id", "trade_date"),
    )

    def __repr__(self):
        return f"<Trade {self.direction} {self.symbol_id} PnL={self.net_pnl}>"
