"""PaperTrade model — Virtual positions for paper trading testing."""
from sqlalchemy import Column, DateTime, Enum, Float, Integer, String
from sqlalchemy.sql import func

from app.core.database import Base


class PaperTrade(Base):
    __tablename__ = "paper_trades"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(30), nullable=False, index=True)
    instrument_key = Column(String(100), nullable=True)
    direction = Column(Enum("BUY", "SELL", name="paper_trade_direction"), nullable=False)
    
    quantity = Column(Integer, nullable=False)
    entry_price = Column(Float, nullable=False)
    stop_loss = Column(Float, nullable=False)
    target_price = Column(Float, nullable=False)
    
    status = Column(Enum("OPEN", "CLOSED", name="paper_trade_status"), default="OPEN", index=True)
    
    exit_price = Column(Float, nullable=True)
    realized_pnl = Column(Float, default=0.0)
    
    close_reason = Column(String(50), nullable=True) # "STOP_LOSS", "TARGET", "MANUAL"

    # TradingModeController: records execution mode at the time of trade creation
    trading_mode = Column(String(10), default="paper", nullable=True)

    # MarketReplayEngine: tags paper trades generated during a replay session
    replay_session_id = Column(String(50), nullable=True, index=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    closed_at = Column(DateTime(timezone=True), nullable=True)

    def __repr__(self):
        return f"<PaperTrade {self.id} {self.direction} {self.quantity} {self.symbol} @ {self.entry_price} [{self.status}]>"

