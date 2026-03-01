"""Candle model — OHLCV Time-Series (TimescaleDB Hypertable)"""
from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Index, Integer, Numeric, String
from sqlalchemy.orm import relationship

from app.core.database import Base


class Candle(Base):
    __tablename__ = "candles"

    time = Column(DateTime(timezone=True), primary_key=True, nullable=False)
    symbol_id = Column(Integer, ForeignKey("symbols.id"), primary_key=True, nullable=False)
    timeframe = Column(String(10), primary_key=True, nullable=False, default="1min")
    open = Column(Numeric(12, 2))
    high = Column(Numeric(12, 2))
    low = Column(Numeric(12, 2))
    close = Column(Numeric(12, 2))
    volume = Column(BigInteger)

    # Relationships
    symbol = relationship("Symbol", back_populates="candles")

    __table_args__ = (
        Index("idx_candles_symbol_time", "symbol_id", "time"),
    )

    def __repr__(self):
        return f"<Candle {self.symbol_id} {self.time} O={self.open} H={self.high} L={self.low} C={self.close}>"
