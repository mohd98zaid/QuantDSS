"""
Market Data router — Upstox-first OHLCV seeding with Yahoo Finance fallback.

Priority:
  1. Upstox v3 Historical/Intraday API  (requires valid UPSTOX_ACCESS_TOKEN)
  2. Yahoo Finance                       (fallback when token missing/expired)

Endpoints:
  POST /market-data/seed/{symbol}?timeframe=   — fetch + store historical candles
  POST /market-data/intraday/{symbol}?timeframe= — fetch + store today's candles
  GET  /market-data/symbols                    — list symbols with candle counts
"""
from __future__ import annotations

from typing import Literal

import pandas as pd
import yfinance as yf
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_session
from app.core.logging import logger
from app.ingestion.upstox_http import UpstoxHTTPClient, UpstoxTokenError
from app.models.candle import Candle
from app.models.symbol import Symbol

router = APIRouter()

TIMEFRAME_LITERAL = Literal[
    "1min", "3min", "5min", "10min", "15min", "30min",
    "1hour", "2hour", "4hour", "1day", "1week", "1month"
]

# Yahoo Finance fallback mappings
YF_INTERVAL: dict[str, str] = {
    "1min": "1m", "3min": "3m", "5min": "5m", "10min": "5m",
    "15min": "15m", "30min": "30m", "1hour": "1h",
    "2hour": "1h", "4hour": "1h", "1day": "1d",
    "1week": "1wk", "1month": "1mo",
}
YF_PERIOD: dict[str, str] = {
    "1min": "5d", "3min": "10d", "5min": "30d", "10min": "30d",
    "15min": "60d", "30min": "60d", "1hour": "180d",
    "2hour": "180d", "4hour": "365d", "1day": "365d",
    "1week": "730d", "1month": "1095d",
}
NSE_SUFFIX = ".NS"
INDEX_MAP = {
    "NIFTY50": "^NSEI", "NIFTY": "^NSEI",
    "BANKNIFTY": "^NSEBANK", "SENSEX": "^BSESN",
}


def _yf_ticker(trading_symbol: str) -> str:
    sym = trading_symbol.upper()
    return INDEX_MAP.get(sym, sym + NSE_SUFFIX)


async def _resolve_symbol(db: AsyncSession, symbol: str) -> Symbol:
    """Look up the Symbol ORM object or raise 404."""
    result = await db.execute(
        select(Symbol).where(Symbol.trading_symbol == symbol.upper())
    )
    sym = result.scalar_one_or_none()
    if not sym:
        raise HTTPException(
            status_code=404,
            detail=f"Symbol '{symbol}' not found. Add it in Settings first.",
        )
    return sym


async def _upsert_candles(db: AsyncSession, rows: list[dict]) -> int:
    """Bulk upsert cantle rows into the candles table. Returns count inserted."""
    if not rows:
        return 0
    stmt = (
        pg_insert(Candle)
        .values(rows)
        .on_conflict_do_update(
            index_elements=["time", "symbol_id", "timeframe"],
            set_={
                "open":   pg_insert(Candle).excluded.open,
                "high":   pg_insert(Candle).excluded.high,
                "low":    pg_insert(Candle).excluded.low,
                "close":  pg_insert(Candle).excluded.close,
                "volume": pg_insert(Candle).excluded.volume,
            },
        )
    )
    await db.execute(stmt)
    await db.commit()
    return len(rows)


# ─── Upstox fetch helpers ────────────────────────────────────────────────────

async def _fetch_upstox_historical(sym: Symbol, timeframe: str) -> list[dict]:
    """Fetch historical candles from Upstox. Returns raw candle dicts."""
    if not sym.instrument_key:
        raise ValueError(f"No instrument_key set for {sym.trading_symbol}. Update it in Settings.")
    client = UpstoxHTTPClient()
    return await client.get_historical_candles(sym.instrument_key, timeframe)


async def _fetch_upstox_intraday(sym: Symbol, timeframe: str) -> list[dict]:
    """Fetch intraday candles from Upstox."""
    if not sym.instrument_key:
        raise ValueError(f"No instrument_key set for {sym.trading_symbol}. Update it in Settings.")
    client = UpstoxHTTPClient()
    return await client.get_intraday_candles(sym.instrument_key, timeframe)


# ─── Yahoo Finance fallback ──────────────────────────────────────────────────

def _fetch_yfinance(trading_symbol: str, timeframe: str) -> list[dict]:
    """Download OHLCV from Yahoo Finance and return candle dicts."""
    ticker = _yf_ticker(trading_symbol)
    interval = YF_INTERVAL.get(timeframe, "5m")
    period = YF_PERIOD.get(timeframe, "30d")

    logger.info(f"Yahoo Finance fallback: {ticker} {interval} ({period})")
    df: pd.DataFrame = yf.download(
        ticker, period=period, interval=interval,
        auto_adjust=True, progress=False, threads=False,
    )
    if df is None or df.empty:
        raise RuntimeError(f"Yahoo Finance returned no data for '{trading_symbol}' ({ticker})")

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.rename(columns=str.capitalize).dropna(subset=["Open", "High", "Low", "Close"])
    df.index = pd.to_datetime(df.index, utc=True)

    candles = []
    for ts, row in df.iterrows():
        candles.append({
            "time":   ts.to_pydatetime(),
            "open":   float(row["Open"]),
            "high":   float(row["High"]),
            "low":    float(row["Low"]),
            "close":  float(row["Close"]),
            "volume": int(row.get("Volume", 0) or 0),
        })
    return candles


def _upstox_to_db_rows(candles: list[dict], sym: Symbol, timeframe: str) -> list[dict]:
    """Convert raw Upstox candle dicts to DB row dicts."""
    from datetime import datetime
    rows = []
    for c in candles:
        t = c["time"]
        if isinstance(t, str):
            # Python 3.11+ handles "2025-01-01T09:15:00+05:30" natively
            t = datetime.fromisoformat(t)
        rows.append({
            "time": t,
            "symbol_id": sym.id,
            "timeframe": timeframe,
            "open":   c["open"],
            "high":   c["high"],
            "low":    c["low"],
            "close":  c["close"],
            "volume": c["volume"],
        })
    return rows


def _yf_to_db_rows(candles: list[dict], sym: Symbol, timeframe: str) -> list[dict]:
    """Convert Yahoo Finance candle dicts to DB row dicts."""
    return [
        {
            "time": c["time"],
            "symbol_id": sym.id,
            "timeframe": timeframe,
            "open":   c["open"],
            "high":   c["high"],
            "low":    c["low"],
            "close":  c["close"],
            "volume": c["volume"],
        }
        for c in candles
    ]


# ─── API Endpoints ────────────────────────────────────────────────────────────

@router.post("/market-data/seed/{symbol}")
async def seed_historical_candles(
    symbol: str,
    timeframe: TIMEFRAME_LITERAL = "5min",
    db: AsyncSession = Depends(get_session),
    _user: dict = Depends(get_current_user),
):
    """
    Fetch historical OHLCV and store in the candles table.

    Priority: Upstox v3 → Yahoo Finance fallback.
    """
    sym = await _resolve_symbol(db, symbol)
    source = "upstox"
    rows: list[dict] = []

    # 1. Try Upstox
    try:
        candles = await _fetch_upstox_historical(sym, timeframe)
        rows = _upstox_to_db_rows(candles, sym, timeframe)
        logger.info(f"Upstox: fetched {len(rows)} candles for {symbol} ({timeframe})")
    except UpstoxTokenError as e:
        logger.warning(f"Upstox token error → falling back to Yahoo Finance: {e}")
        source = "yahoo_finance"
    except ValueError as e:
        # Missing instrument_key — go straight to Yahoo Finance
        logger.warning(f"{e} → falling back to Yahoo Finance")
        source = "yahoo_finance"
    except Exception as e:
        logger.warning(f"Upstox fetch failed ({e}) → falling back to Yahoo Finance")
        source = "yahoo_finance"

    # 2. Yahoo Finance fallback
    if source == "yahoo_finance":
        try:
            yf_candles = _fetch_yfinance(symbol, timeframe)
            rows = _yf_to_db_rows(yf_candles, sym, timeframe)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Both Upstox and Yahoo Finance failed: {exc}")

    count = await _upsert_candles(db, rows)
    return {
        "symbol": symbol.upper(),
        "timeframe": timeframe,
        "candles_seeded": count,
        "source": source,
        "instrument_key": sym.instrument_key or "not_set",
        "from": rows[0]["time"].isoformat() if rows and hasattr(rows[0]["time"], "isoformat") else None,
        "to":   rows[-1]["time"].isoformat() if rows and hasattr(rows[-1]["time"], "isoformat") else None,
    }


@router.post("/market-data/intraday/{symbol}")
async def seed_intraday_candles(
    symbol: str,
    timeframe: Literal["1min", "3min", "5min", "10min", "15min", "30min", "1hour"] = "5min",
    db: AsyncSession = Depends(get_session),
    _user: dict = Depends(get_current_user),
):
    """
    Fetch today's intraday OHLCV from Upstox and store it.
    Only available during/after market hours (Upstox only — no yfinance fallback for intraday).
    """
    sym = await _resolve_symbol(db, symbol)

    try:
        candles = await _fetch_upstox_intraday(sym, timeframe)
    except UpstoxTokenError as e:
        raise HTTPException(status_code=401, detail=f"Upstox token required for intraday data: {e}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Upstox intraday fetch failed: {e}")

    rows = _upstox_to_db_rows(candles, sym, timeframe)
    count = await _upsert_candles(db, rows)
    return {
        "symbol": symbol.upper(),
        "timeframe": timeframe,
        "candles_seeded": count,
        "source": "upstox_intraday",
        "instrument_key": sym.instrument_key or "not_set",
    }


@router.get("/market-data/symbols")
async def list_symbols_with_status(
    db: AsyncSession = Depends(get_session),
    _user: dict = Depends(get_current_user),
):
    """Return all active symbols with candle counts and instrument key status."""
    result = await db.execute(select(Symbol).where(Symbol.is_active == True))  # noqa: E712
    symbols = result.scalars().all()

    # FIX #6: was N+1 (1 COUNT query per symbol). Now single GROUP BY query.
    count_result = await db.execute(
        select(Candle.symbol_id, func.count().label("cnt"))
        .group_by(Candle.symbol_id)
    )
    count_map: dict[int, int] = {row.symbol_id: row.cnt for row in count_result.all()}

    return [
        {
            "id": s.id,
            "trading_symbol": s.trading_symbol,
            "exchange": s.exchange,
            "instrument_key": s.instrument_key,
            "candle_count": count_map.get(s.id, 0),
            "yf_ticker": _yf_ticker(s.trading_symbol),
            "upstox_ready": bool(s.instrument_key),
        }
        for s in symbols
    ]

