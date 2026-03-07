"""Auto-Trader configuration — stored in DB so it persists across restarts."""
from datetime import datetime, timezone
from sqlalchemy import Boolean, Column, Float, Integer, String, DateTime, JSON
from app.core.database import Base

_UTC = timezone.utc


class AutoTradeConfig(Base):
    __tablename__ = "auto_trade_config"

    id = Column(Integer, primary_key=True, default=1)

    # Master switch
    enabled = Column(Boolean, default=False, nullable=False)

    # Trade parameters
    mode = Column(String(10), default="paper", nullable=False)   # "paper" only for now
    sizing_mode = Column(String(10), default="capital", nullable=False)  # "capital" or "quantity"
    qty_per_trade = Column(Integer, default=1, nullable=False)           # used when sizing_mode="quantity"
    capital_per_trade = Column(Float, default=10000.0, nullable=False)   # ₹ used when sizing_mode="capital"
    max_open_positions = Column(Integer, default=3, nullable=False)
    
    # Live execution params
    max_slippage_pct = Column(Float, default=0.001, nullable=False)      # 0.1% max slippage for Limit Orders

    # What to scan (used by scheduled mode / APScheduler backup)
    strategy = Column(String(50), default="ema_crossover", nullable=False)
    timeframe = Column(String(10), default="5min", nullable=False)
    watchlist = Column(JSON, default=list)  # list of symbol strings

    # Scan interval (minutes)
    scan_interval_minutes = Column(Integer, default=5, nullable=False)

    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(_UTC), onupdate=lambda: datetime.now(_UTC))
