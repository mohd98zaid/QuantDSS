from datetime import datetime
from sqlalchemy import Column, String, DateTime, Index
from app.core.database import Base


class ExecutedSignal(Base):
    """
    Tracks signals that have already been executed by the AutoTrader Worker
    to prevent duplicate execution upon worker restarts or overlapping queries.
    """
    __tablename__ = "executed_signals"

    # We use a composite hash or the signal's trace_id as the primary key
    signal_hash = Column(String, primary_key=True)
    symbol = Column(String, nullable=False)
    strategy_id = Column(String, nullable=False)
    direction = Column(String, nullable=False)
    executed_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Index for fast daily cleanup or symbol lookups
    __table_args__ = (
        Index("ix_executed_signals_symbol", "symbol"),
        Index("ix_executed_signals_executed_at", "executed_at"),
    )
