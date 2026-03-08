"""
Signal Scanner — On-demand, stateless signal analysis for ANY NSE stock.

Data priority:
  1. Upstox Historical/Intraday API  (when UPSTOX_ACCESS_TOKEN is configured)
  2. Yahoo Finance                    (fallback — always works, no token needed)

No DB, no watchlist required. Type any symbol → get a signal instantly.

Endpoints:
  GET  /scanner/strategies             — list available strategies
  GET  /scanner/search?q=HDFC         — autocomplete symbol search (from Upstox instruments)
  POST /scanner/analyze                — run signal analysis for any symbol
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from functools import partial
from typing import Literal

import pandas as pd
import yfinance as yf
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.api.deps import get_current_user
from app.core.logging import logger
from app.engine.indicators import IndicatorEngine
from app.engine.strategies.ema_crossover import EMACrossoverStrategy
from app.engine.strategies.rsi_mean_reversion import RSIMeanReversionStrategy
from app.engine.strategies.orb_vwap import ORBVWAPStrategy
from app.engine.strategies.volume_expansion import VolumeExpansionStrategy
from app.engine.strategies.trend_continuation import TrendContinuationStrategy
from app.engine.regime_detector import RegimeDetector
from app.engine.relative_strength import RelativeStrengthScanner
from app.engine.mtf_filter import MultiTimeframeFilter
from app.ingestion.upstox_http import UpstoxHTTPClient, UpstoxTokenError
from app.ingestion.upstox_instruments import instruments_lookup

router = APIRouter()

# ─── Yahoo Finance fallback helpers ──────────────────────────────────────────

NSE_SUFFIX = ".NS"
INDEX_MAP = {
    "NIFTY50": "^NSEI", "NIFTY": "^NSEI",
    "BANKNIFTY": "^NSEBANK", "SENSEX": "^BSESN",
}
YF_INTERVAL: dict[str, str] = {
    "1min": "1m",  "5min": "5m",  "15min": "15m", "30min": "30m",
    "1hour": "1h", "1day": "1d",
}
YF_PERIOD: dict[str, str] = {
    "1min": "5d",  "5min": "30d", "15min": "60d", "30min": "60d",
    "1hour": "180d", "1day": "365d",
}


def _yf_ticker(symbol: str) -> str:
    sym = symbol.upper()
    if sym.endswith(NSE_SUFFIX):
        return sym
    return INDEX_MAP.get(sym, sym + NSE_SUFFIX)


def _fetch_yfinance_df(symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
    """Fetch OHLCV from Yahoo Finance synchronously (called in a thread executor)."""
    ticker = _yf_ticker(symbol)
    interval = YF_INTERVAL.get(timeframe, "5m")
    period = YF_PERIOD.get(timeframe, "30d")

    df: pd.DataFrame = yf.download(
        ticker, period=period, interval=interval,
        auto_adjust=True, progress=False, threads=False,
    )
    if df is None or df.empty:
        raise ValueError(f"No data from Yahoo Finance for '{symbol}' ({ticker}). Check the symbol.")

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.rename(columns=str.capitalize).rename(columns={
        "Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume",
    })
    df = df.dropna(subset=["open", "high", "low", "close"])
    df.index = pd.to_datetime(df.index, utc=True)

    if limit and len(df) > limit:
        df = df.tail(limit)
    return df


async def _fetch_yfinance_async(symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
    """Non-blocking wrapper: runs yfinance download in a thread pool executor."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, partial(_fetch_yfinance_df, symbol, timeframe, limit))


def _upstox_candles_to_df(candles: list[dict], limit: int) -> pd.DataFrame:
    """Convert Upstox candle dicts to a clean DataFrame."""
    from datetime import timezone
    rows = []
    for c in candles:
        t = c["time"]
        if isinstance(t, str):
            from datetime import datetime as dt
            t = dt.fromisoformat(t)
        if hasattr(t, "tzinfo") and t.tzinfo is None:
            from datetime import timezone as tz
            t = t.replace(tzinfo=tz.utc)
        rows.append({
            "time": t,
            "open": float(c["open"]),
            "high": float(c["high"]),
            "low":  float(c["low"]),
            "close": float(c["close"]),
            "volume": int(c.get("volume", 0)),
        })
    df = pd.DataFrame(rows)
    df = df.set_index("time")
    df.index = pd.to_datetime(df.index, utc=True)
    if limit and len(df) > limit:
        df = df.tail(limit)
    return df


async def _fetch_ohlcv(symbol: str, timeframe: str, limit: int) -> tuple[pd.DataFrame, str]:
    """
    Fetch OHLCV for any symbol. Returns (DataFrame, source_name).

    Priority:
      1. Upstox (historical + intraday blend) — if token + instrument_key available
      2. Angel One SmartAPI               — if credentials configured
      3. Yahoo Finance fallback            — always works, no auth
    """
    # ── 1. Try Upstox ─────────────────────────────────────────────────────────
    try:
        instrument_key = await instruments_lookup.get_instrument_key(symbol)
        if instrument_key:
            logger.info(f"Scanner: resolved {symbol} → {instrument_key}")
            client = UpstoxHTTPClient()

            from datetime import datetime as dt, timezone as tz
            from datetime import timedelta
            IST = tz(timedelta(hours=5, minutes=30))
            now_ist = dt.now(IST)
            market_open = now_ist.replace(hour=9, minute=15, second=0, microsecond=0)
            market_close = now_ist.replace(hour=15, minute=30, second=0, microsecond=0)
            is_market_hours = market_open <= now_ist <= market_close and now_ist.weekday() < 5

            all_candles: list[dict] = []

            # Fetch historical candles
            hist = await client.get_historical_candles(instrument_key, timeframe)
            all_candles.extend(hist)

            # During market hours, also fetch today's intraday to get live data
            if is_market_hours and timeframe not in ("1day",):
                try:
                    intraday = await client.get_intraday_candles(instrument_key, timeframe)
                    # Merge: drop any historical candles from today, replace with intraday
                    today_str = now_ist.strftime("%Y-%m-%d")
                    all_candles = [
                        c for c in all_candles
                        if not str(c["time"]).startswith(today_str)
                    ]
                    all_candles.extend(intraday)
                    logger.info(f"Scanner: merged intraday for {symbol} (market open)")
                except Exception as e:
                    logger.warning(f"Scanner: intraday merge skipped ({e})")

            if all_candles:
                df = _upstox_candles_to_df(all_candles, limit)
                return df, "upstox"
            else:
                logger.warning(f"Upstox returned 0 candles for {symbol} → Angel One fallback")

    except UpstoxTokenError as e:
        logger.warning(f"Scanner: Upstox token error for {symbol} → Angel One fallback: {e}")
    except Exception as e:
        logger.warning(f"Scanner: Upstox fetch error for {symbol} ({e}) → Angel One fallback")

    # ── 2. Angel One fallback ──────────────────────────────────────────────────
    try:
        from app.ingestion.angel_http import AngelOneHTTPClient, AngelOneError
        angel = AngelOneHTTPClient()
        candles = await angel.get_candles_by_symbol(symbol, timeframe)
        if candles:
            df = _upstox_candles_to_df(candles, limit)
            logger.info(f"Scanner: Angel One returned {len(df)} candles for {symbol}")
            return df, "angel_one"
        logger.warning(f"Angel One returned 0 candles for {symbol} → Yahoo fallback")
    except Exception as e:
        logger.warning(f"Scanner: Angel One fetch error for {symbol} ({e}) → Yahoo fallback")

    # ── 3. Yahoo Finance fallback ──────────────────────────────────────────────
    logger.info(f"Scanner: fetching {symbol} from Yahoo Finance")
    try:
        df = await _fetch_yfinance_async(symbol, timeframe, limit)
        return df, "yahoo_finance"
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Data fetch failed for '{symbol}': {e}")


# ─── Strategy registry ────────────────────────────────────────────────────────

STRATEGIES = {
    "ema_crossover": {
        "name": "EMA Crossover",
        "description": "Trend-following. Fires on golden/death cross of EMA9 × EMA21 with volume confirmation.",
        "default_params": {
            "ema_fast": 9, "ema_slow": 21, "atr_period": 14,
            "volume_ma_period": 20,
            "atr_multiplier_sl": 1.5, "atr_multiplier_target": 3.0,
        },
        "min_candles": 26,
    },
    "rsi_mean_reversion": {
        "name": "RSI Mean Reversion",
        "description": "Pullback entries. Buys oversold bounces in uptrend; sells overbought rejections in downtrend.",
        "default_params": {
            "rsi_period": 14, "ema_trend": 50, "atr_period": 14,
            "rsi_oversold": 35, "rsi_overbought": 65,
            "atr_multiplier_sl": 1.0, "risk_reward": 2.0,
        },
        "min_candles": 55,
    },
    "orb_vwap": {
        "name": "ORB + VWAP",
        "description": "Opening Range Breakout (9:15-9:30 AM) confirmed by VWAP, EMA9>EMA21, and volume spike.",
        "default_params": {
            "ema_fast": 9, "ema_slow": 21, "atr_period": 14,
            "volume_ma_period": 10,
            "atr_multiplier_sl": 1.0, "risk_reward": 2.0,
            "volume_factor": 1.0,
        },
        "min_candles": 30,
    },
    "volume_expansion": {
        "name": "Volume Expansion",
        "description": "Detects abnormal volume spikes (>3× avg) with 5-bar high/low breakout and expanding ATR.",
        "default_params": {
            "volume_ma_period": 20, "atr_period": 14,
            "vol_multiplier": 3.0, "lookback_bars": 5,
            "atr_multiplier_sl": 1.0, "risk_reward": 1.5,
        },
        "min_candles": 30,
    },
    "trend_continuation": {
        "name": "Trend Continuation",
        "description": "Full EMA9>21>50 triple alignment + pullback to VWAP + bullish/bearish candle close.",
        "default_params": {
            "ema_slow": 50, "atr_period": 14,
            "vwap_tolerance_atr": 0.3,
            "atr_multiplier_sl": 1.0, "risk_reward": 2.0,
        },
        "min_candles": 60,
    },
    "multi_strategy": {
        "name": "Multi-Strategy (All)",
        "description": "Runs ALL strategies simultaneously and returns any signals found.",
        "default_params": {},
        "min_candles": 60,
    },
}


# ─── Pydantic schemas ─────────────────────────────────────────────────────────

StrategyLiteral = Literal[
    "ema_crossover", "rsi_mean_reversion", "orb_vwap",
    "volume_expansion", "trend_continuation", "multi_strategy"
]


class ScanRequest(BaseModel):
    symbol: str
    strategy: StrategyLiteral = "ema_crossover"
    timeframe: Literal["1min", "5min", "15min", "30min", "1hour", "1day"] = "5min"
    candles_limit: int = 150


class SignalResult(BaseModel):
    signal: str
    entry_price: float
    stop_loss: float
    target_price: float
    risk_reward: float
    atr: float
    strategy_name: str
    candle_time: str
    confidence_score: float = 0.0   # 0–100: how strongly conditions are met


class ScanResponse(BaseModel):
    symbol: str
    timeframe: str
    strategy: str
    ltp: float
    change_pct: float
    candles_fetched: int
    signals: list[SignalResult]
    indicators: dict
    data_source: str
    instrument_key: str | None
    scanned_at: str


# ─── Core strategy execution ──────────────────────────────────────────────────

def _run_strategy(
    strategy_key: str,
    df: pd.DataFrame,
    regime: str = "TREND",
) -> list[SignalResult]:
    all_keys = ["ema_crossover", "rsi_mean_reversion", "orb_vwap", "volume_expansion", "trend_continuation"]
    strategies_to_run = all_keys if strategy_key == "multi_strategy" else [strategy_key]

    results = []
    for key in strategies_to_run:
        # Skip strategies disabled by the current regime
        from app.engine.regime_detector import RegimeDetector
        if not RegimeDetector.is_strategy_allowed(key, regime):
            logger.info(f"_run_strategy: skipping {key} (regime={regime})")
            continue

        if key not in STRATEGIES:
            continue
        meta   = STRATEGIES[key]
        params = meta["default_params"]

        if key == "ema_crossover":
            strat = EMACrossoverStrategy(strategy_id=1, params=params)
        elif key == "rsi_mean_reversion":
            strat = RSIMeanReversionStrategy(strategy_id=2, params=params)
        elif key == "orb_vwap":
            strat = ORBVWAPStrategy(strategy_id=3, params=params)
        elif key == "volume_expansion":
            strat = VolumeExpansionStrategy(strategy_id=4, params=params)
        elif key == "trend_continuation":
            strat = TrendContinuationStrategy(strategy_id=5, params=params)
        else:
            continue

        signal = strat.evaluate(df.copy(), symbol_id=0)
        if signal:
            ct = signal.candle_time
            results.append(SignalResult(
                signal=signal.signal_type,
                entry_price=round(float(signal.entry_price), 2),
                stop_loss=round(float(signal.stop_loss), 2),
                target_price=round(float(signal.target_price), 2),
                risk_reward=float(signal.risk_reward),
                atr=round(float(signal.atr_value), 4),
                strategy_name=meta["name"],
                candle_time=ct.isoformat() if hasattr(ct, "isoformat") else str(ct),
                confidence_score=round(float(signal.confidence_score), 1),
            ))
    return results


def _extract_indicators(df: pd.DataFrame) -> dict:
    close = df["close"]

    def safe(series: pd.Series) -> float | None:
        v = series.iloc[-1]
        return round(float(v), 4) if not pd.isna(v) else None

    rsi    = IndicatorEngine.rsi(close, 14)
    ema9   = IndicatorEngine.ema(close, 9)
    ema21  = IndicatorEngine.ema(close, 21)
    ema50  = IndicatorEngine.ema(close, 50)
    atr    = IndicatorEngine.atr(df["high"], df["low"], close, 14)
    vol_ma = IndicatorEngine.volume_ma(df["volume"], 20)
    vwap   = IndicatorEngine.vwap(df["high"], df["low"], close, df["volume"])

    current_volume = int(df["volume"].iloc[-1])
    vol_ma_val = safe(vol_ma)
    relative_volume = round(current_volume / vol_ma_val, 2) if vol_ma_val and vol_ma_val > 0 else None

    snap: dict = {
        "rsi_14": safe(rsi),
        "ema_9": safe(ema9),
        "ema_21": safe(ema21),
        "ema_50": safe(ema50),
        "atr_14": safe(atr),
        "vwap": safe(vwap),
        "volume_ma_20": vol_ma_val,
        "relative_volume": relative_volume,
        "volume": current_volume,
    }

    ltp = float(close.iloc[-1])
    if snap["ema_50"]:
        snap["trend"] = "UPTREND" if ltp > snap["ema_50"] else "DOWNTREND"
    if snap["ema_9"] and snap["ema_21"] and snap["ema_50"]:
        e9, e21, e50 = snap["ema_9"], snap["ema_21"], snap["ema_50"]
        if e9 > e21 > e50:
            snap["ema_alignment"] = "TRIPLE_BULL"
        elif e9 < e21 < e50:
            snap["ema_alignment"] = "TRIPLE_BEAR"
        elif e9 > e21:
            snap["ema_alignment"] = "BULLISH"
        else:
            snap["ema_alignment"] = "BEARISH"
    if snap["rsi_14"]:
        r = snap["rsi_14"]
        snap["rsi_zone"] = (
            "OVERBOUGHT" if r >= 70 else
            "OVERSOLD"   if r <= 30 else
            "STRONG"     if r >= 60 else
            "WEAK"       if r <= 40 else
            "NEUTRAL"
        )
    return snap


# ─── API Endpoints ────────────────────────────────────────────────────────────

@router.get("/scanner/strategies")
async def list_scanner_strategies(_user: dict = Depends(get_current_user)):
    """List all available scanner strategies."""
    return [
        {"key": k, "name": v["name"], "description": v["description"], "min_candles": v["min_candles"]}
        for k, v in STRATEGIES.items()
    ]


@router.get("/scanner/search")
async def search_symbols(
    q: str = Query(..., min_length=1, description="Symbol or company name to search"),
    limit: int = Query(15, ge=1, le=50),
    _user: dict = Depends(get_current_user),
):
    """
    Autocomplete symbol search using Upstox NSE instruments master.

    Returns list of {symbol, name, key, exchange, lot_size}.
    The instruments list is downloaded once and cached for 24 hours.
    """
    results = await instruments_lookup.search(q, limit=limit)
    return {
        "query": q,
        "results": results,
        "instruments_loaded": instruments_lookup.symbol_count,
        "source": "upstox_instruments_master",
    }


@router.post("/scanner/analyze", response_model=ScanResponse)
async def scan_symbol(
    req: ScanRequest,
    _user: dict = Depends(get_current_user),
):
    """
    On-demand signal analysis for any NSE symbol.

    Data priority: Upstox (historical + live intraday) → Yahoo Finance fallback.
    No DB, no watchlist required.
    """
    symbol = req.symbol.strip().upper()
    if not symbol:
        raise HTTPException(status_code=400, detail="Symbol cannot be empty")

    # Fetch OHLCV (Upstox first, Yahoo fallback)
    df, source = await _fetch_ohlcv(symbol, req.timeframe, req.candles_limit)

    if len(df) < 5:
        raise HTTPException(
            status_code=422,
            detail=f"Insufficient data: only {len(df)} candles for '{symbol}'. Try a longer timeframe."
        )

    ltp = float(df["close"].iloc[-1])
    open_price = float(df["open"].iloc[0])
    change_pct = ((ltp - open_price) / open_price * 100) if open_price > 0 else 0.0

    signals = []
    indicators = _extract_indicators(df)
    # Detect regime; filter strategies based on it
    try:
        regime = RegimeDetector().detect(df)
        signals = _run_strategy(req.strategy, df, regime=regime)
        indicators["regime"] = regime
    except Exception as e:
        logger.warning(f"Regime detection failed for {symbol}: {e}")
        signals = _run_strategy(req.strategy, df)


    # Resolve instrument key for response metadata
    instrument_key = await instruments_lookup.get_instrument_key(symbol)

    # ── Auto-Trader hook (same as bulk_scan) ─────────────────────────
    signal_results = [
        BulkScanResult(
            symbol=symbol, ltp=round(ltp, 2), change_pct=round(change_pct, 2),
            signal=sig.signal, entry_price=sig.entry_price,
            stop_loss=sig.stop_loss, target_price=sig.target_price,
            risk_reward=sig.risk_reward, strategy_name=sig.strategy_name,
            rsi=indicators.get("rsi_14"), trend=indicators.get("trend"),
            ema_cross=indicators.get("ema_cross"),
            data_source=source, error=None,
        )
        for sig in signals if sig.signal in ("BUY", "SELL")
    ]
    if signal_results:
        await _auto_trade_hook(signal_results, req.strategy, req.timeframe)

    return ScanResponse(
        symbol=symbol,
        timeframe=req.timeframe,
        strategy=STRATEGIES.get(req.strategy, {}).get("name", req.strategy),
        ltp=round(ltp, 2),
        change_pct=round(change_pct, 2),
        candles_fetched=len(df),
        signals=signals,
        indicators=indicators,
        data_source=source,
        instrument_key=instrument_key,
        scanned_at=datetime.now(UTC).isoformat(),
    )


async def _save_signals_to_db(results: list, strategy: str, timeframe: str) -> None:
    """Persist BUY/SELL scanner results to the `signals` table."""
    from app.core.database import async_session_factory
    from app.models.signal import Signal
    from app.models.symbol import Symbol
    from sqlalchemy import select

    try:
        async with async_session_factory() as db:
            # Build a symbol lookup map once
            sym_names = [r.symbol for r in results]
            sym_result = await db.execute(
                select(Symbol).where(Symbol.trading_symbol.in_(sym_names))
            )
            sym_map = {s.trading_symbol: s.id for s in sym_result.scalars().all()}

            for r in results:
                db.add(Signal(
                    symbol_id=sym_map.get(r.symbol),      # None if not in watchlist — OK
                    signal_type=r.signal,
                    entry_price=r.entry_price or 0,
                    stop_loss=r.stop_loss or 0,
                    target_price=r.target_price or 0,
                    risk_reward=r.risk_reward or 0,
                    risk_status="APPROVED",
                    block_reason=None,
                ))
            await db.commit()
    except Exception as e:
        logger.warning(f"_save_signals_to_db failed (non-critical): {e}")


async def _auto_trade_hook(results: list, strategy: str, timeframe: str) -> None:
    """
    Safe wrapper called after a scan:
    1. Persists signals to the signals DB table → Signals page.
    2. Publishes to SSE stream → Dashboard Live Signal Feed.
    3. Feeds signals into the Intelligence Pipeline (Fix C-06).
       The intelligence pipeline handles: consolidation → confirmation →
       quality scoring → ML → NLP → time filter → final alert → AutoTrader.
    4. Falls back to direct AutoTrader if intelligence pipeline is not available.
    """
    from datetime import datetime, timezone
    from app.alerts.sse_manager import SSEManager

    logger.info(f"--- AUTO TRADE HOOK CALLED ---\nStrategy: {strategy}\nTimeframe: {timeframe}\nResults: {results}")

    # ── 1. Save to DB ────────────────────────────────────────────────────
    await _save_signals_to_db(results, strategy, timeframe)

    # ── 2. Publish to SSE dashboard feed ─────────────────────────────────
    for r in results:
        try:
            await SSEManager.publish_signal_event({
                "signal_type": r.signal,
                "symbol": r.symbol,
                "strategy": strategy,
                "timeframe": timeframe,
                "entry_price": float(r.entry_price or 0),
                "stop_loss": float(r.stop_loss or 0),
                "target_price": float(r.target_price or 0),
                "risk_reward": float(r.risk_reward or 0),
                "rsi": float(r.rsi) if r.rsi is not None else None,
                "trend": r.trend,
                "change_pct": float(r.change_pct or 0),
                "risk_status": "APPROVED",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
        except Exception as e:
            logger.debug(f"SSE publish failed for {r.symbol}: {e}")

    # ── 3. Feed into Intelligence Pipeline via SignalPool (Fix C-06) ─────
    # Corrective Refactor: ALL scanner signals go through the intelligence
    # pipeline.  The old direct-to-AutoTrader fallback has been REMOVED.
    try:
        from app.engine.signal_pool import signal_pool
        from app.engine.base_strategy import CandidateSignal
        from app.engine.signal_dedup import signal_dedup
        from app.engine.signal_trace import SignalTracer

        trace_id = SignalTracer.new_trace_id()
        fed = 0
        for r in results:
            if r.signal not in ("BUY", "SELL"):
                continue
            # Convert BulkScanResult → CandidateSignal for the intelligence pipeline
            candidate = CandidateSignal(
                symbol_id=0,  # Scanner doesn't have DB symbol ID
                strategy_id=0,
                strategy_name=strategy,
                signal_type=r.signal,
                entry_price=float(r.entry_price or 0),
                stop_loss=float(r.stop_loss or 0),
                target_price=float(r.target_price or 0),
                atr_value=0.0,
                candle_time=datetime.now(timezone.utc),
                confidence_score=float(r.risk_reward or 0) * 20,  # Approx score
                metadata={"symbol_name": r.symbol, "source": "scanner", "trace_id": trace_id},
            )
            # Dedup check (Implicitly records if it wasn't a duplicate)
            if await signal_dedup.is_duplicate(candidate.symbol_id, candidate.strategy_id, candidate.candle_time):
                SignalTracer.trace_drop(trace_id, "DEDUP_CHECK", r.symbol, "Duplicate from scanner suppressed")
                continue
            await signal_pool.add_signal(candidate)
            fed += 1
        if fed:
            SignalTracer.trace_pass(trace_id, "SIGNAL_POOL", strategy, f"{fed} scanner signal(s) queued")
            logger.info(f"C-06: {fed} scanner signal(s) routed to intelligence pipeline (trace={trace_id})")
    except Exception as e:
        logger.warning(f"Intelligence pipeline routing failed: {e}")




# ─── Preset symbol lists ──────────────────────────────────────────────────────

PRESET_LISTS: dict[str, list[str]] = {
    "nifty50": [
        "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "HINDUNILVR",
        "ITC", "SBIN", "BHARTIARTL", "KOTAKBANK", "LT", "AXISBANK",
        "ASIANPAINT", "MARUTI", "BAJFINANCE", "WIPRO", "HCLTECH", "SUNPHARMA",
        "TITAN", "ULTRACEMCO", "BAJAJFINSV", "NTPC", "POWERGRID", "ONGC",
        "ADANIENT", "TATAMOTORS", "TATASTEEL", "JSWSTEEL", "HINDALCO",
        "COALINDIA", "GRASIM", "DRREDDY", "CIPLA", "DIVISLAB", "APOLLOHOSP",
        "BRITANNIA", "EICHERMOT", "NESTLEIND", "HEROMOTOCO", "BPCL",
        "TECHM", "INDUSINDBK", "M&M", "TATACONSUM", "BAJAJ-AUTO",
        "SBILIFE", "HDFCLIFE", "ADANIPORTS", "SHREECEM", "UPL",
    ],
    "banknifty": [
        "HDFCBANK", "ICICIBANK", "KOTAKBANK", "SBIN", "AXISBANK",
        "INDUSINDBK", "BANDHANBNK", "IDFCFIRSTB", "AUBANK", "FEDERALBNK",
        "PNB", "BANKBARODA",
    ],
    "niftyit": [
        "TCS", "INFY", "HCLTECH", "WIPRO", "TECHM",
        "MPHASIS", "LTTS", "PERSISTENT", "COFORGE", "OFSS",
    ],
    "fno_actives": [
        "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK",
        "SBIN", "AXISBANK", "TATAMOTORS", "ADANIENT", "BAJFINANCE",
        "ONGC", "WIPRO", "MARUTI", "TITAN", "JSWSTEEL",
        "HINDALCO", "TATASTEEL", "NTPC", "COALINDIA", "M&M",
    ],
    "midcap": [
        "INDIAMART", "PAGEIND", "ABFRL", "BATAINDIA", "VOLTAS",
        "MFSL", "POLYCAB", "ASTRAL", "ALKEM", "PIIND",
        "GLAND", "GMRINFRA", "RADICO", "LALPATHLAB", "METROPOLIS",
    ],
    "psu": [
        "ONGC", "NTPC", "COALINDIA", "POWERGRID", "SBIN",
        "BPCL", "BHEL", "GAIL", "NHPC", "NMDC",
        "SAIL", "NALCO", "RECLTD", "PFC", "IRCTC",
        "IRFC", "CONCOR", "BEL", "HAL", "RVNL",
    ],
}


class BulkScanRequest(BaseModel):
    list_name: str = "nifty50"
    custom_symbols: list[str] = []
    strategy: Literal[
        "ema_crossover", "rsi_mean_reversion", "orb_vwap",
        "volume_expansion", "trend_continuation", "multi_strategy"
    ] = "ema_crossover"
    timeframe: Literal["1min", "5min", "15min", "30min", "1hour", "1day"] = "5min"
    signals_only: bool = True


class BulkScanResult(BaseModel):
    symbol: str
    ltp: float
    change_pct: float
    signal: str
    entry_price: float
    stop_loss: float
    target_price: float
    risk_reward: float
    strategy_name: str
    rsi: float | None
    trend: str | None
    ema_cross: str | None
    signal_quality_score: float | None = None
    ml_probability: float | None = None
    market_regime: str | None = None
    sentiment: str | None = None
    strategies_confirmed: list[str] | None = None
    data_source: str
    error: str | None


class BulkScanResponse(BaseModel):
    list_name: str
    strategy: str
    timeframe: str
    total_scanned: int
    signals_found: int
    results: list[BulkScanResult]
    scanned_at: str


async def _scan_one(symbol: str, strategy: str, timeframe: str) -> BulkScanResult:
    """Scan a single symbol and return a compact BulkScanResult."""
    try:
        df, source = await _fetch_ohlcv(symbol, timeframe, limit=150)
        if len(df) < 5:
            return BulkScanResult(
                symbol=symbol, ltp=0, change_pct=0, signal="NEUTRAL",
                entry_price=0, stop_loss=0, target_price=0, risk_reward=0,
                strategy_name="", rsi=None, trend=None, ema_cross=None,
                data_source=source, error="Insufficient data",
            )

        ltp = float(df["close"].iloc[-1])
        open_p = float(df["open"].iloc[0])
        change_pct = ((ltp - open_p) / open_p * 100) if open_p > 0 else 0.0

        signals = _run_strategy(strategy, df)
        indicators = _extract_indicators(df)

        if signals:
            sig = signals[0]
            return BulkScanResult(
                symbol=symbol, ltp=round(ltp, 2), change_pct=round(change_pct, 2),
                signal=sig.signal,
                entry_price=sig.entry_price, stop_loss=sig.stop_loss,
                target_price=sig.target_price, risk_reward=sig.risk_reward,
                strategy_name=sig.strategy_name,
                rsi=indicators.get("rsi_14"),
                trend=indicators.get("trend"),
                ema_cross=indicators.get("ema_cross"),
                data_source=source, error=None,
            )
        return BulkScanResult(
            symbol=symbol, ltp=round(ltp, 2), change_pct=round(change_pct, 2),
            signal="NEUTRAL", entry_price=ltp, stop_loss=0, target_price=0,
            risk_reward=0, strategy_name="",
            rsi=indicators.get("rsi_14"),
            trend=indicators.get("trend"),
            ema_cross=indicators.get("ema_cross"),
            data_source=source, error=None,
        )
    except Exception as e:
        logger.error(f"Scanner error for {symbol}: {e}")
        return BulkScanResult(
            symbol=symbol, ltp=0, change_pct=0, signal="NEUTRAL",
            entry_price=0, stop_loss=0, target_price=0, risk_reward=0,
            strategy_name="", rsi=None, trend=None, ema_cross=None,
            data_source="error", error="An unexpected error occurred during scan.",
        )


@router.get("/scanner/lists")
async def list_presets(_user: dict = Depends(get_current_user)):
    """List all available stock preset lists."""
    return [
        {"key": k, "name": k.replace("_", " ").title(), "count": len(v)}
        for k, v in PRESET_LISTS.items()
    ]


@router.post("/scanner/bulk", response_model=BulkScanResponse)
async def bulk_scan(
    req: BulkScanRequest,
    _user: dict = Depends(get_current_user),
):
    """
    Auto-scan a preset list of NSE stocks concurrently.

    Runs up to 8 stocks in parallel. Returns only signalling stocks by default.
    Typical time: ~5-8 seconds for 50 stocks.
    """
    import asyncio

    symbols = (
        [s.strip().upper() for s in req.custom_symbols if s.strip()]
        if req.custom_symbols
        else PRESET_LISTS.get(req.list_name, PRESET_LISTS["nifty50"])
    )
    if not symbols:
        raise HTTPException(status_code=400, detail="No symbols to scan")

    logger.info(f"Bulk scan: {len(symbols)} symbols, strategy={req.strategy}, tf={req.timeframe}")

    sem = asyncio.Semaphore(5)  # up to 5 concurrent stocks to be safe on API limits

    async def _bounded(sym: str) -> BulkScanResult:
        async with sem:
            return await _scan_one(sym, req.strategy, req.timeframe)

    results: list[BulkScanResult] = list(await asyncio.gather(*[_bounded(s) for s in symbols]))

    signal_order = {"BUY": 0, "SELL": 1, "NEUTRAL": 2}
    results.sort(key=lambda r: (signal_order.get(r.signal, 9), -abs(r.change_pct)))

    display = [r for r in results if r.signal in ("BUY", "SELL")] if req.signals_only else results

    # ── Auto-Trader reactive hook ─────────────────────────────────────
    # Awaited so the auto-trade logic is guaranteed to run before the response
    signal_results = [r for r in results if r.signal in ("BUY", "SELL")]
    if signal_results:
        await _auto_trade_hook(signal_results, req.strategy, req.timeframe)

    return BulkScanResponse(
        list_name=req.list_name if not req.custom_symbols else "custom",
        strategy=STRATEGIES.get(req.strategy, {}).get("name", req.strategy),
        timeframe=req.timeframe,
        total_scanned=len(results),
        signals_found=sum(1 for r in results if r.signal in ("BUY", "SELL")),
        results=display,
        scanned_at=datetime.now(UTC).isoformat(),
    )
