"""AuditLog model — Immutable Audit Trail"""
from sqlalchemy import BigInteger, Column, DateTime, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import JSON
from sqlalchemy.sql import func

from app.core.database import Base


class AuditLog(Base):
    __tablename__ = "audit_log"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now())
    event_type = Column(String(50), nullable=False)    # SIGNAL_GENERATED, TRADE_LOGGED, RISK_HALTED, etc.
    entity_type = Column(String(50), nullable=True)    # signals, trades, daily_risk_state
    entity_id = Column(Integer, nullable=True)
    payload = Column(JSON().with_variant(JSONB, 'postgresql'), nullable=True)             # Full state snapshot at time of event
    source = Column(String(50), default="system")      # system / user

    __table_args__ = (
        Index("idx_audit_timestamp", "timestamp"),
        Index("idx_audit_event", "event_type", "timestamp"),
    )

    def __repr__(self):
        return f"<AuditLog {self.event_type} entity={self.entity_type}:{self.entity_id}>"
