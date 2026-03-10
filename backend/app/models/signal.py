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
    # Phase 1: Signal Scoring Engine
    confidence_score = Column(Integer, nullable=True)          # 0–100
    confidence_tier  = Column(String(10), nullable=True)       # HIGH / MEDIUM / REJECT
    score_breakdown  = Column(Text, nullable=True)             # JSON breakdown
    # Phase 5: Latency tracking
    latency_ms = Column(Integer, nullable=True)                # tick-to-signal ms
    # Phase 8: Intelligence pipeline metadata (Corrective Refactor)
    ml_probability = Column(Numeric(6, 4), nullable=True)      # ML model confidence
    sentiment = Column(String(20), nullable=True)               # NLP sentiment
    strategies_confirmed = Column(Text, nullable=True)          # JSON list of strategy names
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    strategy = relationship("Strategy", back_populates="signals")
    symbol = relationship("Symbol", back_populates="signals")
    # NOTE: Signal.trades relationship intentionally omitted — Trade.signal_id has no FK
    # to signals table (removed to avoid circular FK issues). Join manually if needed.

    __table_args__ = (
        Index("idx_signals_timestamp", "timestamp"),
        Index("idx_signals_symbol", "symbol_id", "timestamp"),
        Index("idx_signals_status", "risk_status", "timestamp"),
    )

    def __repr__(self):
        return f"<Signal {self.signal_type} {self.risk_status} @ {self.entry_price}>"
