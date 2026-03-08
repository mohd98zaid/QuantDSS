"""
OrderEvent — Audit model for complete order lifecycle tracking.

Captures every state transition of an order:
  - placed, acknowledged, filled, partially_filled
  - sl_placed, tp_placed, sl_triggered, tp_triggered
  - cancelled, rejected, error

Usage:
    from app.models.order_event import OrderEvent
    event = OrderEvent(
        order_id="ORD123",
        event_type="placed",
        payload_json={"qty": 10, "price": 250.50},
        source="execution_manager",
    )
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Column, Integer, String, DateTime, JSON

from app.core.database import Base


class OrderEvent(Base):
    """Audit trail for order lifecycle events."""
    __tablename__ = "order_event"

    id = Column(Integer, primary_key=True, autoincrement=True)
    order_id = Column(String(100), nullable=False, index=True)
    event_type = Column(String(50), nullable=False, index=True)
    event_timestamp = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )
    payload_json = Column(JSON)
    source = Column(String(100), default="execution_manager")
