"""
AngelOneHTTPClient — Thin REST client for Angel One SmartAPI.

Handles:
- Session login (auto-refreshed, TOTP-based — no daily manual token rotation)
- Historical candle data — OHLCV for any timeframe up to 365 days
- Intraday candle data  — today's OHLCV candles

Data priority in scanner/candle endpoints:
  1. Upstox  (primary — live intraday during market hours)
  2. Angel One  (THIS FILE — auto-session, good historical data)
  3. Yahoo Finance  (final fallback — no auth)

Raises AngelOneError so callers can fall back gracefully.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from functools import partial
from typing import Any

import httpx

from app.core.config import settings
from app.core.logging import logger

ANGEL_BASE = "https://apiconnect.angelbroking.com"
IST = timezone(timedelta(hours=5, minutes=30))

# Angel One interval strings
TIMEFRAME_TO_ANGEL: dict[str, str] = {
    "1min":  "ONE_MINUTE",
    "3min":  "THREE_MINUTE",
    "5min":  "FIVE_MINUTE",
    "10min": "TEN_MINUTE",
    "15min": "FIFTEEN_MINUTE",
    "30min": "THIRTY_MINUTE",
    "1hour": "ONE_HOUR",
    "1day":  "ONE_DAY",
}

# How many days of history to request per timeframe
HISTORY_DAYS: dict[str, int] = {
    "1min":  5,
    "3min":  7,
    "5min":  30,
    "10min": 30,
    "15min": 60,
    "30min": 60,
    "1hour": 180,
    "1day":  365,
}

# Angel One NSE instrument master (downloaded once, cached)
_INSTRUMENT_CACHE: dict[str, str] = {}   # symbol → symboltoken
_INSTRUMENT_LOADED = False


class AngelOneError(Exception):
    """Raised when the Angel One session is invalid or candle fetch fails."""


class AngelOneHTTPClient:
    """
    Stateless HTTP client for Angel One SmartAPI candle data.

    Session token is cached at the module level and re-used until it expires.
    Login is triggered automatically on first use or after expiry.

    Usage:
        client = AngelOneHTTPClient()
        candles = await client.get_candles_by_symbol("RELIANCE", "5min")
    """

    # Class-level session cache (shared across all instances in the process)
    _session_token: str | None = None
    _session_expiry: datetime | None = None
    _login_lock = asyncio.Lock()

    # ── Credential check ──────────────────────────────────────────────────────

    @staticmethod
    def _credentials_available() -> bool:
        return bool(
            settings.angel_api_key
            and settings.angel_client_id
            and settings.angel_password
            and settings.angel_totp_secret
        )

    # ── Session management ────────────────────────────────────────────────────

    @classmethod
    async def _ensure_session(cls) -> str:
        """Return a valid JWT token, logging in if needed."""
        now = datetime.now(IST)
        if cls._session_token and cls._session_expiry and now < cls._session_expiry:
            return cls._session_token

        async with cls._login_lock:
            # Double-check after acquiring lock
            if cls._session_token and cls._session_expiry and now < cls._session_expiry:
                return cls._session_token

            if not cls._credentials_available():
                raise AngelOneError(
                    "Angel One credentials not set in .env "
                    "(ANGEL_API_KEY, ANGEL_CLIENT_ID, ANGEL_PASSWORD, ANGEL_TOTP_SECRET)"
                )

            token = await asyncio.get_event_loop().run_in_executor(None, cls._do_login)
            cls._session_token = token
            # Angel One tokens are valid until midnight IST — expire them at 23:59
            cls._session_expiry = now.replace(hour=23, minute=59, second=0, microsecond=0)
            if now >= cls._session_expiry:
                cls._session_expiry += timedelta(days=1)
            logger.info("AngelOne: session refreshed, valid until %s", cls._session_expiry)
            return token

    @staticmethod
    def _do_login() -> str:
        """Synchronous login — runs in executor thread to avoid blocking."""
        import pyotp

        totp = pyotp.TOTP(settings.angel_totp_secret).now()
        payload = {
            "clientcode": settings.angel_client_id,
            "password":   settings.angel_password,
            "totp":       totp,
        }
        headers = {
            "Content-Type":        "application/json",
            "Accept":              "application/json",
            "X-UserType":          "USER",
            "X-SourceID":          "WEB",
            "X-ClientLocalIP":     "127.0.0.1",
            "X-ClientPublicIP":    "127.0.0.1",
            "X-MACAddress":        "00:00:00:00:00:00",
            "X-PrivateKey":        settings.angel_api_key or "",
        }
        resp = httpx.post(
            f"{ANGEL_BASE}/rest/auth/angelbroking/user/v1/loginByPassword",
            json=payload, headers=headers, timeout=20,
        )
        if not resp.is_success:
            raise AngelOneError(f"Angel One login failed ({resp.status_code}): {resp.text[:300]}")
        body = resp.json()
        if not body.get("status"):
            raise AngelOneError(f"Angel One login rejected: {body.get('message', 'unknown error')}")
        token = body.get("data", {}).get("jwtToken")
        if not token:
            raise AngelOneError("Angel One login succeeded but no jwtToken in response")
        logger.info("AngelOne: login successful for %s", settings.angel_client_id)
        return token

    _instrument_lock = asyncio.Lock()

    # ── Instrument token lookup ───────────────────────────────────────────────

    @classmethod
    async def _resolve_symbol_token(cls, symbol: str) -> tuple[str, str]:
        """
        Resolve NSE symbol name → (symboltoken, exchange).
        Downloads and caches the Angel One instrument master JSON.

        Returns:
            (symboltoken, exchange)  e.g. ("2885", "NSE")
        """
        global _INSTRUMENT_CACHE, _INSTRUMENT_LOADED

        if not _INSTRUMENT_LOADED:
            async with cls._instrument_lock:
                if not _INSTRUMENT_LOADED:
                    await asyncio.get_event_loop().run_in_executor(
                        None, _load_angel_instruments
                    )

        token = _INSTRUMENT_CACHE.get(symbol.upper())
        if not token:
            raise AngelOneError(
                f"Symbol '{symbol}' not found in Angel One instrument master. "
                "Try the full NSE symbol name (e.g. HDFCBANK, not HDFC BANK)."
            )
        return token, "NSE"

    # ── Candle data ───────────────────────────────────────────────────────────

    async def get_candles(
        self,
        symbol_token: str,
        exchange: str,
        timeframe: str,
        from_date: str,
        to_date: str,
    ) -> list[dict[str, Any]]:
        """
        Fetch OHLCV candles from Angel One getCandleData API.

        Args:
            symbol_token: Angel One instrument token, e.g. "2885"
            exchange:     "NSE" or "BSE"
            timeframe:    one of the TIMEFRAME_TO_ANGEL keys
            from_date:    "YYYY-MM-DD HH:MM"
            to_date:      "YYYY-MM-DD HH:MM"

        Returns:
            [{time, open, high, low, close, volume}] in chronological order
        """
        if timeframe not in TIMEFRAME_TO_ANGEL:
            raise AngelOneError(f"Unsupported timeframe for Angel One: {timeframe}")

        jwt = await self._ensure_session()
        interval = TIMEFRAME_TO_ANGEL[timeframe]

        payload = {
            "exchange":    exchange,
            "symboltoken": symbol_token,
            "interval":    interval,
            "fromdate":    from_date,
            "todate":      to_date,
        }
        headers = {
            "Content-Type":     "application/json",
            "Accept":           "application/json",
            "X-UserType":       "USER",
            "X-SourceID":       "WEB",
            "X-ClientLocalIP":  "127.0.0.1",
            "X-ClientPublicIP": "127.0.0.1",
            "X-MACAddress":     "00:00:00:00:00:00",
            "X-PrivateKey":     settings.angel_api_key or "",
            "Authorization":    f"Bearer {jwt}",
        }

        logger.info(
            "AngelOne: getCandleData %s %s %s → %s [%s]",
            exchange, symbol_token, from_date, to_date, interval,
        )

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{ANGEL_BASE}/rest/secure/angelbroking/historical/v1/getCandleData",
                json=payload, headers=headers,
            )

        if resp.status_code == 401:
            # Clear cached token so next call re-logs in
            AngelOneHTTPClient._session_token = None
            raise AngelOneError("Angel One session expired (401) — will re-login on next attempt")
        if not resp.is_success:
            raise AngelOneError(
                f"Angel One getCandleData error {resp.status_code}: {resp.text[:300]}"
            )

        body = resp.json()
        if not body.get("status"):
            raise AngelOneError(f"Angel One API error: {body.get('message', 'unknown')}")

        raw_candles: list[list] = body.get("data", []) or []
        return _parse_angel_candles(raw_candles)

    async def get_candles_by_symbol(self, symbol: str, timeframe: str) -> list[dict[str, Any]]:
        """
        Convenience wrapper: resolve symbol → token, then fetch candles.

        Args:
            symbol:    NSE symbol e.g. "RELIANCE"
            timeframe: one of the supported timeframe keys

        Returns:
            [{time, open, high, low, close, volume}] in chronological order
        """
        symbol_token, exchange = await self._resolve_symbol_token(symbol)
        now_ist = datetime.now(IST)
        days = HISTORY_DAYS.get(timeframe, 30)
        from_dt = now_ist - timedelta(days=days)

        # Angel One date format: "YYYY-MM-DD HH:MM"
        from_date = from_dt.strftime("%Y-%m-%d 09:15")
        to_date   = now_ist.strftime("%Y-%m-%d 15:30")

        candles = await self.get_candles(symbol_token, exchange, timeframe, from_date, to_date)
        logger.info("AngelOne: %d candles returned for %s %s", len(candles), symbol, timeframe)
        return candles


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_angel_candles(raw: list[list]) -> list[dict[str, Any]]:
    """
    Convert Angel One candle arrays to dicts.
    Angel One format: [timestamp_str, open, high, low, close, volume]
    """
    result = []
    for row in raw:
        if not row or len(row) < 6:
            continue
        try:
            result.append({
                "time":   row[0],          # "2024-01-15T09:15:00+05:30"
                "open":   float(row[1]),
                "high":   float(row[2]),
                "low":    float(row[3]),
                "close":  float(row[4]),
                "volume": int(row[5]),
            })
        except (TypeError, ValueError):
            continue
    return result   # Angel One returns chronological order already


def _load_angel_instruments() -> None:
    """
    Download and cache the Angel One OpenAPI instrument master.
    URL: https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json
    Cached in memory for the lifetime of the process.
    """
    global _INSTRUMENT_CACHE, _INSTRUMENT_LOADED

    url = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
    logger.info("AngelOne: downloading instrument master from %s", url)
    try:
        resp = httpx.get(url, timeout=30, follow_redirects=True)
        resp.raise_for_status()
        instruments: list[dict] = resp.json()
        cache: dict[str, str] = {}
        for inst in instruments:
            exch = (inst.get("exch_seg") or "").upper()
            if exch != "NSE":
                continue
            sym = (inst.get("symbol") or "").upper().replace("-EQ", "").strip()
            token = str(inst.get("token") or "")
            if sym and token:
                cache[sym] = token
        _INSTRUMENT_CACHE = cache
        _INSTRUMENT_LOADED = True
        logger.info("AngelOne: instrument master loaded — %d NSE symbols", len(cache))
    except Exception as e:
        logger.error("AngelOne: failed to load instrument master: %s", e)
        _INSTRUMENT_LOADED = True   # mark loaded (even if empty) to avoid repeated retries
