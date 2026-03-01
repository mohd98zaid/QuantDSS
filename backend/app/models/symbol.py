"""Symbol model — Instruments Watchlist"""
from sqlalchemy import BigInteger, Boolean, Column, DateTime, Index, Integer, Numeric, String
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.core.database import Base


class Symbol(Base):
    __tablename__ = "symbols"

    id = Column(Integer, primary_key=True, autoincrement=True)
    trading_symbol = Column(String(50), nullable=False, unique=True)
    exchange = Column(String(10), nullable=False, default="NSE")
    instrument_token = Column(BigInteger, nullable=True)
    lot_size = Column(Integer, default=1)
    tick_size = Column(Numeric(10, 4), nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    strategy_symbols = relationship("StrategySymbol", back_populates="symbol")
    candles = relationship("Candle", back_populates="symbol")
    signals = relationship("Signal", back_populates="symbol")
    trades = relationship("Trade", back_populates="symbol")

    __table_args__ = (
        Index("idx_symbols_active", "is_active"),
    )

    def __repr__(self):
        return f"<Symbol {self.trading_symbol} ({self.exchange})>"
