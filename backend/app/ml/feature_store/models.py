"""
Feature Store Models — SQLAlchemy models for ML feature storage.

Tables:
  feature_snapshot      — Point-in-time feature vectors per symbol per candle
  feature_symbol_stats  — Aggregated statistics per symbol
  feature_regime_stats  — Feature distributions per market regime
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Column, Integer, String, Float, DateTime, Text, JSON
from app.core.database import Base


class FeatureSnapshot(Base):
    """Point-in-time feature vector for a symbol."""
    __tablename__ = "feature_snapshot"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(50), nullable=False, index=True)
    timestamp = Column(DateTime(timezone=True), nullable=False, index=True)

    # Indicator features
    ema_9 = Column(Float)
    ema_21 = Column(Float)
    rsi_14 = Column(Float)
    atr_14 = Column(Float)
    macd_line = Column(Float)
    macd_signal = Column(Float)
    vwap = Column(Float)

    # Volatility features
    bollinger_upper = Column(Float)
    bollinger_lower = Column(Float)
    volatility_pct = Column(Float)
    atr_pct = Column(Float)

    # Regime features
    regime = Column(String(30))  # TREND, RANGE, HIGH_VOLATILITY, LOW_LIQUIDITY
    trend_strength = Column(Float)

    # Liquidity features
    volume = Column(Integer)
    volume_ratio = Column(Float)  # current / avg
    spread_pct = Column(Float)

    # Custom features (JSON for extensibility)
    extra_features = Column(JSON)

    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )


class FeatureSymbolStats(Base):
    """Aggregated feature statistics per symbol (daily rollup)."""
    __tablename__ = "feature_symbol_stats"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(50), nullable=False, index=True)
    date = Column(DateTime(timezone=True), nullable=False, index=True)

    avg_volume = Column(Float)
    avg_spread_pct = Column(Float)
    avg_volatility = Column(Float)
    avg_rsi = Column(Float)
    avg_atr_pct = Column(Float)
    candle_count = Column(Integer)

    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )


class FeatureRegimeStats(Base):
    """Feature distributions per market regime."""
    __tablename__ = "feature_regime_stats"

    id = Column(Integer, primary_key=True, autoincrement=True)
    regime = Column(String(30), nullable=False, index=True)
    date = Column(DateTime(timezone=True), nullable=False, index=True)

    symbol_count = Column(Integer)
    avg_volume = Column(Float)
    avg_volatility = Column(Float)
    avg_trend_strength = Column(Float)

    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
