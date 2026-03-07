"""
UpstoxHTTPClient — Thin REST client for Upstox v2/v3 Market Data APIs.

Handles:
- Historical candle data (v3)  — past OHLCV for any timeframe
- Intraday candle data (v3)    — today's OHLCV candles (live)
- Exchange / market status     — real open/close from NSE
- LTP + OHLC quotes            — live price quotes

All calls use the Bearer token from settings.upstox_access_token.
Raises UpstoxTokenError (401) so callers can fall back gracefully.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import httpx

from app.core.config import settings
from app.core.logging import logger

UPSTOX_BASE = "https://api.upstox.com"

# Our timeframe names → (unit, interval) for Upstox v3 API
TIMEFRAME_TO_UPSTOX: dict[str, tuple[str, str]] = {
    "1min":  ("minutes", "1"),
    "3min":  ("minutes", "3"),
    "5min":  ("minutes", "5"),
    "10min": ("minutes", "10"),
    "15min": ("minutes", "15"),
    "30min": ("minutes", "30"),
    "1hour": ("hours", "1"),
    "2hour": ("hours", "2"),
    "4hour": ("hours", "4"),
    "1day":  ("days", "1"),
    "1week": ("weeks", "1"),
    "1month": ("months", "1"),
}

# How many days to fetch per timeframe (historical mode)
HISTORY_DAYS: dict[str, int] = {
    "1min":  5,
    "3min":  10,
    "5min":  20,
    "10min": 20,
    "15min": 20,
    "30min": 60,
    "1hour": 180,
    "2hour": 180,
    "4hour": 365,
    "1day":  365,
    "1week": 730,
    "1month": 1095,
}

IST = timezone(timedelta(hours=5, minutes=30))


class UpstoxTokenError(Exception):
    """Raised when the Upstox access token is missing or expired (401)."""


class UpstoxHTTPClient:
    """
    Stateless REST client for Upstox market data APIs.

    Usage:
        client = UpstoxHTTPClient()
        candles = await client.get_historical_candles("NSE_EQ|INE848E01016", "5min")
    """

    def __init__(self, access_token: str | None = None):
        self._token = access_token or settings.upstox_access_token

    def _headers(self) -> dict[str, str]:
        if not self._token:
            raise UpstoxTokenError("UPSTOX_ACCESS_TOKEN is not set in .env")
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._token}",
        }

    def _parse_candles(self, raw: list[list]) -> list[dict]:
        """
        Convert Upstox candle arrays to dicts.
        Format: [timestamp, open, high, low, close, volume, oi]
        """
        result = []
        for row in raw:
            if len(row) < 6:
                continue
            result.append({
                "time":   row[0],           # ISO-8601 string with IST offset
                "open":   float(row[1]),
                "high":   float(row[2]),
                "low":    float(row[3]),
                "close":  float(row[4]),
                "volume": int(row[5]),
                "oi":     int(row[6]) if len(row) > 6 else 0,
            })
        return result

    async def get_historical_candles(
        self,
        instrument_key: str,
        timeframe: str = "5min",
        days_back: int | None = None,
    ) -> list[dict]:
        """
        Fetch historical OHLCV from Upstox v3 Historical Candle API.

        Args:
            instrument_key: e.g. "NSE_EQ|INE848E01016"  (URL-encode | → %7C)
            timeframe: one of TIMEFRAME_TO_UPSTOX keys
            days_back: override default history window

        Returns:
            List of candles [{time, open, high, low, close, volume, oi}]
        """
        if timeframe not in TIMEFRAME_TO_UPSTOX:
            raise ValueError(f"Unsupported timeframe: {timeframe}")

        unit, interval = TIMEFRAME_TO_UPSTOX[timeframe]
        days = days_back or HISTORY_DAYS.get(timeframe, 30)

        now_ist = datetime.now(IST)
        to_date = now_ist.strftime("%Y-%m-%d")
        from_date = (now_ist - timedelta(days=days)).strftime("%Y-%m-%d")

        # Upstox requires | to be URL-encoded as %7C
        encoded_key = instrument_key.replace("|", "%7C")
        url = f"{UPSTOX_BASE}/v3/historical-candle/{encoded_key}/{unit}/{interval}/{to_date}/{from_date}"

        logger.info(f"Upstox historical fetch: {instrument_key} {timeframe} {from_date}→{to_date}")

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, headers=self._headers())

        if resp.status_code == 401:
            raise UpstoxTokenError(f"Upstox token expired or invalid (401): {resp.text[:200]}")
        if not resp.is_success:
            raise RuntimeError(f"Upstox historical API error {resp.status_code}: {resp.text[:300]}")

        data = resp.json()
        candles_raw = data.get("data", {}).get("candles", [])
        candles = self._parse_candles(candles_raw)
        # Upstox returns descending order — reverse to chronological
        candles.reverse()
        logger.info(f"Upstox returned {len(candles)} historical candles for {instrument_key}")
        return candles

    async def get_intraday_candles(
        self,
        instrument_key: str,
        timeframe: str = "5min",
    ) -> list[dict]:
        """
        Fetch today's intraday OHLCV from Upstox v3 Intraday Candle API.

        Args:
            instrument_key: e.g. "NSE_EQ|INE848E01016"
            timeframe: one of TIMEFRAME_TO_UPSTOX keys

        Returns:
            List of candles for today [{time, open, high, low, close, volume}]
        """
        if timeframe not in TIMEFRAME_TO_UPSTOX:
            raise ValueError(f"Unsupported timeframe: {timeframe}")

        unit, interval = TIMEFRAME_TO_UPSTOX[timeframe]
        encoded_key = instrument_key.replace("|", "%7C")
        url = f"{UPSTOX_BASE}/v3/historical-candle/intraday/{encoded_key}/{unit}/{interval}"

        logger.info(f"Upstox intraday fetch: {instrument_key} {timeframe}")

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, headers=self._headers())

        if resp.status_code == 401:
            raise UpstoxTokenError(f"Upstox token expired (401): {resp.text[:200]}")
        if not resp.is_success:
            raise RuntimeError(f"Upstox intraday API error {resp.status_code}: {resp.text[:300]}")

        data = resp.json()
        candles_raw = data.get("data", {}).get("candles", [])
        candles = self._parse_candles(candles_raw)
        candles.reverse()  # chronological order
        logger.info(f"Upstox intraday: {len(candles)} candles for {instrument_key}")
        return candles

    async def get_market_status(self, exchange: str = "NSE") -> dict:
        """
        Fetch real exchange status from Upstox API.

        Returns:
            {"exchange": "NSE", "status": "NORMAL_OPEN", "is_open": True}
        """
        url = f"{UPSTOX_BASE}/v2/market/status/{exchange}"

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, headers=self._headers())

        if resp.status_code == 401:
            raise UpstoxTokenError("Upstox token expired (401) — market status unavailable")
        if not resp.is_success:
            raise RuntimeError(f"Upstox market status error {resp.status_code}: {resp.text[:200]}")

        data = resp.json().get("data", {})
        status_str = data.get("status", "")
        is_open = "OPEN" in status_str.upper()
        return {
            "exchange": data.get("exchange", exchange),
            "status": status_str,
            "is_open": is_open,
            "source": "upstox",
        }

    async def get_ltp(self, instrument_keys: list[str]) -> dict[str, float]:
        """
        Fetch Last Traded Price for one or more instruments.

        Args:
            instrument_keys: list of instrument keys e.g. ["NSE_EQ|INE848E01016"]

        Returns:
            {instrument_key: ltp_float}
        """
        # Upstox expects comma-separated, URL-encoded keys
        keys_param = ",".join(k.replace("|", "%7C") for k in instrument_keys)
        url = f"{UPSTOX_BASE}/v3/market-quote/ltp?instrument_key={keys_param}"

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, headers=self._headers())

        if resp.status_code == 401:
            raise UpstoxTokenError("Upstox token expired (401)")
        if not resp.is_success:
            raise RuntimeError(f"Upstox LTP error {resp.status_code}: {resp.text[:200]}")

        result = {}
        data = resp.json().get("data", {})
        for key, val in data.items():
            # Upstox returns key as "NSE_EQ:RELIANCE" in response
            ltp = val.get("last_price") or val.get("ltp")
            if ltp is not None:
                result[key] = float(ltp)
        return result
