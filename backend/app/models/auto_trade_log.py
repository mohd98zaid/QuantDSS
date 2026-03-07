"""Auto-Trader log — records every open/skip/close action with full signal details."""
from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Float, DateTime
from app.core.database import Base

_UTC = timezone.utc


class AutoTradeLog(Base):
    __tablename__ = "auto_trade_log"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    timestamp   = Column(DateTime(timezone=True), default=lambda: datetime.now(_UTC), nullable=False)

    symbol      = Column(String(30), nullable=False)
    signal      = Column(String(10))          # BUY / SELL
    action      = Column(String(20), nullable=False)  # OPEN / SKIP / CLOSE / ERROR / DISABLED_DROP
    reason      = Column(String(200))         # why SKIP / error message

    # Signal details (mirroring scanner output)
    entry_price  = Column(Float)
    stop_loss    = Column(Float)
    target_price = Column(Float)
    risk_reward  = Column(Float)
    rsi          = Column(Float)
    trend        = Column(String(20))         # UPTREND / DOWNTREND

    # Meta
    strategy     = Column(String(50))
    timeframe    = Column(String(10))
    trade_id     = Column(Integer)             # FK to paper_trade.id (if opened)

    # TradingModeController: which mode was active when this action was taken
    trading_mode = Column(String(10), nullable=True)   # "disabled", "paper", "live"
