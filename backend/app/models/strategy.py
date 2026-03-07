"""Strategy models — Strategy Definitions + Strategy-Symbol Mapping"""
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import JSON
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.core.database import Base


class Strategy(Base):
    __tablename__ = "strategies"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    type = Column(String(50), nullable=True)  # trend_following, mean_reversion
    description = Column(Text, nullable=True)
    parameters = Column(JSON().with_variant(JSONB, 'postgresql'), nullable=False)
    is_active = Column(Boolean, default=True)
    min_backtest_trades = Column(Integer, default=30)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    strategy_symbols = relationship("StrategySymbol", back_populates="strategy")
    signals = relationship("Signal", back_populates="strategy")

    def __repr__(self):
        return f"<Strategy {self.name} ({self.type})>"


class StrategySymbol(Base):
    __tablename__ = "strategy_symbols"

    id = Column(Integer, primary_key=True, autoincrement=True)
    strategy_id = Column(Integer, ForeignKey("strategies.id", ondelete="CASCADE"), nullable=False)
    symbol_id = Column(Integer, ForeignKey("symbols.id", ondelete="CASCADE"), nullable=False)
    timeframe = Column(String(10), default="1min")
    is_active = Column(Boolean, default=True)

    # Relationships
    strategy = relationship("Strategy", back_populates="strategy_symbols")
    symbol = relationship("Symbol", back_populates="strategy_symbols")

    __table_args__ = (
        UniqueConstraint("strategy_id", "symbol_id", name="uq_strategy_symbol"),
        Index("idx_ss_strategy", "strategy_id", "is_active"),
    )

    def __repr__(self):
        return f"<StrategySymbol strategy={self.strategy_id} symbol={self.symbol_id}>"
