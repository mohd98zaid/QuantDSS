"""
RelativeStrengthScanner — Computes stock relative strength vs NIFTY 50 index.

RS Score = (stock_return_N / index_return_N) for the last N bars.
  > 1.0 → stock outperforming index (bullish edge)
  < 1.0 → stock underperforming (bearish edge)
  = 1.0 → in-line with market

The NIFTY 50 data is fetched from Yahoo Finance (^NSEI) as a fallback.
When Upstox token is available, it uses NSE_INDEX|Nifty 50 instrument key.
"""
import asyncio
from functools import partial

import pandas as pd

from app.core.logging import logger


# NIFTY 50 Yahoo Finance ticker
NIFTY_YF_TICKER = "^NSEI"
NIFTY_UPSTOX_KEY = "NSE_INDEX|Nifty 50"


async def _fetch_nifty_candles(period: str = "5d", interval: str = "1m") -> pd.DataFrame | None:
    """Fetch NIFTY 50 candles from Yahoo Finance."""
    try:
        import yfinance as yf
        loop = asyncio.get_event_loop()
        data = await loop.run_in_executor(
            None,
            partial(yf.download, NIFTY_YF_TICKER, period=period, interval=interval, progress=False),
        )
        if data is None or data.empty:
            return None
        data.columns = [c.lower() for c in data.columns]
        data.index.name = "time"
        return data.reset_index()
    except Exception as e:
        logger.warning(f"RelativeStrength: NIFTY fetch failed: {e}")
        return None


def compute_rs_score(
    stock_candles: pd.DataFrame,
    index_candles: pd.DataFrame,
    lookback: int = 20,
) -> float:
    """
    Compute the Relative Strength score for a stock vs index.

    Args:
        stock_candles: OHLCV DataFrame for the stock (1-min or daily)
        index_candles: OHLCV DataFrame for NIFTY 50
        lookback: Number of bars to compute return over

    Returns:
        RS score (float). >1 = outperforming, <1 = underperforming.
    """
    try:
        stock_close = stock_candles["close"].dropna()
        index_close = index_candles["close"].dropna()

        if len(stock_close) < lookback + 1 or len(index_close) < lookback + 1:
            return 1.0  # Neutral if insufficient data

        stock_start = float(stock_close.iloc[-lookback - 1])
        stock_end   = float(stock_close.iloc[-1])
        index_start = float(index_close.iloc[-lookback - 1])
        index_end   = float(index_close.iloc[-1])

        if stock_start <= 0 or index_start <= 0:
            return 1.0

        stock_return = (stock_end - stock_start) / stock_start
        index_return = (index_end - index_start) / index_start

        if index_return == 0:
            return 1.0 if stock_return == 0 else (2.0 if stock_return > 0 else 0.0)

        rs_score = (1 + stock_return) / (1 + index_return)
        return round(rs_score, 4)

    except Exception as e:
        logger.warning(f"RelativeStrength: compute_rs_score error: {e}")
        return 1.0


class RelativeStrengthScanner:
    """
    Scanner utility for computing relative strength vs NIFTY 50.

    Usage:
        scanner = RelativeStrengthScanner()
        rs_data = await scanner.get_rs_scores(stock_df_map, lookback=20)
    """

    def __init__(self, lookback: int = 20):
        self.lookback = lookback
        self._nifty_cache: pd.DataFrame | None = None

    async def get_nifty(self) -> pd.DataFrame | None:
        """Fetch and cache NIFTY 50 candles."""
        if self._nifty_cache is None or len(self._nifty_cache) == 0:
            self._nifty_cache = await _fetch_nifty_candles()
        return self._nifty_cache

    async def get_rs_score(self, stock_candles: pd.DataFrame) -> float:
        """Get RS score for a single stock vs NIFTY 50."""
        nifty = await self.get_nifty()
        if nifty is None:
            return 1.0
        return compute_rs_score(stock_candles, nifty, self.lookback)

    @staticmethod
    def interpret(rs_score: float) -> str:
        """Human-readable interpretation of RS score."""
        if rs_score >= 1.2:
            return "STRONG_OUTPERFORMER"
        elif rs_score >= 1.05:
            return "OUTPERFORMER"
        elif rs_score >= 0.95:
            return "MARKET_PERFORMER"
        elif rs_score >= 0.8:
            return "UNDERPERFORMER"
        else:
            return "STRONG_UNDERPERFORMER"
