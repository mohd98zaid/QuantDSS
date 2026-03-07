"""LiveTrade model — Real positions for live trading execution."""
from sqlalchemy import Column, DateTime, Enum, Float, Integer, String
from sqlalchemy.sql import func

from app.core.database import Base


class LiveTrade(Base):
    __tablename__ = "live_trades"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(30), nullable=False, index=True)
    instrument_key = Column(String(100), nullable=True)
    direction = Column(Enum("BUY", "SELL", name="live_trade_direction"), nullable=False)
    
    # Intended order state
    quantity = Column(Integer, nullable=False)
    entry_price = Column(Float, nullable=False)
    stop_loss = Column(Float, nullable=False)
    target_price = Column(Float, nullable=False)
    
    # Actual broker order state
    broker_order_id = Column(String(100), nullable=True, index=True)
    filled_quantity = Column(Integer, default=0)
    average_price = Column(Float, nullable=True)

    # Fix C-04: SL/Target order tracking — used by execution_manager.py
    sl_order_id = Column(String(100), nullable=True)
    target_order_id = Column(String(100), nullable=True)
    risk_amount = Column(Float, nullable=True)
    
    status = Column(Enum("PENDING", "OPEN", "PARTIALLY_FILLED", "REJECTED", "CANCELLED", "CLOSED", name="live_trade_status"), default="PENDING", index=True)
    
    exit_price = Column(Float, nullable=True)
    realized_pnl = Column(Float, default=0.0)
    
    close_reason = Column(String(50), nullable=True) # "STOP_LOSS", "TARGET", "MANUAL", "SIGNAL_EXIT", "EOD_FORCE_CLOSE"
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    closed_at = Column(DateTime(timezone=True), nullable=True)

    def __repr__(self):
        return f"<LiveTrade {self.id} {self.direction} {self.quantity} {self.symbol} @ {self.entry_price} [{self.status}]>"
