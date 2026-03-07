"""RiskConfig model — System Risk Configuration (Singleton)"""
from sqlalchemy import Column, DateTime, Integer, Numeric, String
from sqlalchemy.sql import func

from app.core.database import Base


class RiskConfig(Base):
    __tablename__ = "risk_config"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # ── Position Sizing ────────────────────────────────────────────
    risk_per_trade_pct = Column(Numeric(6, 4), default=0.01)        # 1%
    max_position_pct   = Column(Numeric(6, 4), default=0.20)        # Max 20% of balance per position

    # ── Loss Limits ────────────────────────────────────────────────
    max_daily_loss_inr    = Column(Numeric(12, 2), default=500.00)
    max_daily_loss_pct    = Column(Numeric(6, 4), default=0.02)     # 2%
    max_weekly_loss_inr   = Column(Numeric(12, 2), default=2000.00) # NEW: weekly loss cap ₹
    max_weekly_loss_pct   = Column(Numeric(6, 4), default=0.05)     # NEW: weekly loss cap 5%
    max_account_drawdown_pct = Column(Numeric(6, 4), default=0.10)  # 10% from peak

    # ── Signal Filters ─────────────────────────────────────────────
    cooldown_minutes     = Column(Integer, default=15)
    min_atr_pct          = Column(Numeric(6, 4), default=0.003)     # 0.3%
    max_atr_pct          = Column(Numeric(6, 4), default=0.030)     # 3.0%
    min_risk_reward      = Column(Numeric(5, 2), default=1.50)      # NEW: minimum 1.5:1 R:R
    max_signals_per_stock = Column(Integer, default=3)              # NEW: persisted (was in-memory)
    signal_start_hour    = Column(Integer, default=9)
    signal_start_minute  = Column(Integer, default=20)
    signal_end_hour      = Column(Integer, default=14)
    signal_end_minute    = Column(Integer, default=30)

    # ── Position / Concurrency Limits ─────────────────────────────
    max_concurrent_positions  = Column(Integer, default=2)
    max_correlated_positions  = Column(Integer, default=3)          # Max positions in same sector
    max_consecutive_errors    = Column(Integer, default=5)          # NEW: consecutive API error limit

    # ── Liquidity / Spread ─────────────────────────────────────────
    min_daily_volume = Column(Integer, default=500000)              # Min 500k shares
    max_spread_pct   = Column(Numeric(6, 4), default=0.005)         # Max 0.5% bid-ask spread

    # ── Market Regime ─────────────────────────────────────────────
    market_regime = Column(String(20), default="NONE")              # TRENDING, RANGING, HIGH_VOLATILITY, NONE

    # ── Paper Trading ─────────────────────────────────────────────
    paper_balance = Column(Numeric(12, 2), default=100000.00)       # Virtual Capital

    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Singleton enforcement is done via unique index in migration

    def __repr__(self):
        return f"<RiskConfig risk={self.risk_per_trade_pct} daily_cap={self.max_daily_loss_inr}>"
