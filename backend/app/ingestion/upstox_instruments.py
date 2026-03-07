"""
Upstox Instruments Master — in-memory lookup table for NSE/BSE symbols.

Downloads the official Upstox instruments CSV from the public CDN URL,
parses it into a {trading_symbol → instrument_key} dictionary, and
caches it in process memory with a 24-hour TTL.

No API token required — the instruments file is publicly accessible.

Usage:
    from app.ingestion.upstox_instruments import instruments_lookup

    key = await instruments_lookup.get_instrument_key("RELIANCE")
    # → "NSE_EQ|INE002A01018"

    results = await instruments_lookup.search("HDFC", limit=10)
    # → [{"symbol": "HDFCBANK", "name": "HDFC Bank", "key": "NSE_EQ|...", ...}, ...]
"""
from __future__ import annotations

import asyncio
import gzip
import io
import time
from typing import Any

import httpx
import pandas as pd

from app.core.logging import logger

# Upstox public instruments CSV URLs (no auth needed)
NSE_CSV_URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.csv.gz"
BSE_CSV_URL = "https://assets.upstox.com/market-quote/instruments/exchange/BSE.csv.gz"

# Cache TTL: 24 hours
_CACHE_TTL = 86_400


class UpstoxInstrumentsLookup:
    """
    Builds and caches an in-memory NSE instruments lookup from Upstox's
    public instruments master CSV.

    The CSV contains columns like:
        instrument_key, tradingsymbol, name, exchange, lot_size, ...

    We build two indexes:
        _by_symbol: {"RELIANCE": {"key": "NSE_EQ|INE002A01018", "name": "...", ...}}
        _search_list: sorted list of (symbol, name, key) tuples for fuzzy search
    """

    def __init__(self) -> None:
        self._by_symbol: dict[str, dict[str, Any]] = {}
        self._search_list: list[dict[str, Any]] = []
        self._loaded_at: float = 0.0
        self._load_failed_at: float = 0.0  # track last failure for fast retry
        self._loading = False
        self._load_event: asyncio.Event | None = None  # concurrent waiters

    def _is_stale(self) -> bool:
        return (time.time() - self._loaded_at) > _CACHE_TTL

    async def _load(self) -> None:
        """Download and parse the Upstox NSE instruments CSV."""
        if self._loading:
            return
        self._loading = True
        self._load_event = asyncio.Event()
        try:
            logger.info("Upstox instruments: downloading NSE master CSV…")
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(NSE_CSV_URL)
                resp.raise_for_status()

            # Decompress gzip
            raw = gzip.decompress(resp.content)
            df = pd.read_csv(io.BytesIO(raw), low_memory=False)

            # Normalise column names (Upstox CSV uses mixed case)
            df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]

            # Required columns
            required = {"instrument_key", "tradingsymbol"}
            if not required.issubset(set(df.columns)):
                # Try alternate column names
                rename_map = {}
                for col in df.columns:
                    if "instrument" in col and "key" in col:
                        rename_map[col] = "instrument_key"
                    if "trading" in col or col == "symbol":
                        rename_map[col] = "tradingsymbol"
                df = df.rename(columns=rename_map)

            # Filter to equity + index instruments only (skip futures/options)
            if "instrument_type" in df.columns:
                df = df[df["instrument_type"].isin(["EQUITY", "INDEX", "EQ"])]
            elif "segment" in df.columns:
                df = df[df["segment"].isin(["NSE_EQ", "NSE_INDEX", "BSE_EQ"])]

            # Drop rows with missing keys
            df = df.dropna(subset=["instrument_key", "tradingsymbol"])
            df["tradingsymbol"] = df["tradingsymbol"].str.strip().str.upper()
            df["instrument_key"] = df["instrument_key"].str.strip()

            by_symbol: dict[str, dict[str, Any]] = {}
            search_list: list[dict[str, Any]] = []

            for _, row in df.iterrows():
                sym = str(row["tradingsymbol"])
                key = str(row["instrument_key"])
                name_raw = row.get("name", row.get("company_name", sym))
                name = str(name_raw).strip() if name_raw and str(name_raw) != "nan" else sym
                exchange = str(row.get("exchange", "NSE"))
                lot_raw = row.get("lot_size", 1)
                try:
                    lot = int(float(lot_raw)) if pd.notna(lot_raw) else 1
                except (ValueError, TypeError):
                    lot = 1

                entry = {
                    "symbol": sym,
                    "name": name,
                    "key": key,
                    "exchange": exchange,
                    "lot_size": lot,
                }
                # Prefer NSE_EQ over others when duplicates exist
                if sym not in by_symbol or "NSE_EQ" in key:
                    by_symbol[sym] = entry
                search_list.append(entry)

            self._by_symbol = by_symbol
            self._search_list = sorted(search_list, key=lambda x: x["symbol"])
            self._loaded_at = time.time()
            logger.info(
                f"Upstox instruments: loaded {len(by_symbol):,} unique symbols "
                f"({len(search_list):,} total entries)"
            )
        except Exception as e:
            logger.error(f"Upstox instruments fetch failed: {e}")
            self._load_failed_at = time.time()  # so we retry after 5 min
        finally:
            self._loading = False
            if self._load_event:
                self._load_event.set()  # wake up all waiters
                self._load_event = None

    async def ensure_loaded(self) -> None:
        """Ensure the instruments list is loaded and fresh. Thread-safe via asyncio.Event."""
        # Already loaded and not stale
        if self._by_symbol and not self._is_stale():
            return

        # Another coroutine is currently loading — wait for it
        if self._loading and self._load_event:
            await self._load_event.wait()
            return

        # Don't retry too fast after a failure (wait 5 minutes)
        if self._load_failed_at and (time.time() - self._load_failed_at) < 300:
            return

        await self._load()

    async def get_instrument_key(self, symbol: str) -> str | None:
        """
        Resolve a trading symbol to its Upstox instrument_key.

        Args:
            symbol: e.g. "RELIANCE", "INFY", "BANKNIFTY"

        Returns:
            instrument_key string, or None if not found.
        """
        await self.ensure_loaded()
        entry = self._by_symbol.get(symbol.upper().strip())
        return entry["key"] if entry else None

    async def search(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        """
        Fuzzy search across all NSE symbols and names.

        Matches:
          1. Exact symbol match (highest priority)
          2. Symbol starts-with
          3. Symbol contains
          4. Name contains

        Returns list of {symbol, name, key, exchange, lot_size}.
        """
        await self.ensure_loaded()
        q = query.upper().strip()
        if not q:
            return []

        exact: list[dict] = []
        starts: list[dict] = []
        contains: list[dict] = []
        name_matches: list[dict] = []

        seen: set[str] = set()

        for entry in self._search_list:
            sym = entry["symbol"]
            name = entry["name"].upper()
            key = entry["key"]

            if key in seen:
                continue

            if sym == q:
                exact.append(entry)
                seen.add(key)
            elif sym.startswith(q):
                starts.append(entry)
                seen.add(key)
            elif q in sym:
                contains.append(entry)
                seen.add(key)
            elif q in name:
                name_matches.append(entry)
                seen.add(key)

        combined = exact + starts + contains + name_matches
        return combined[:limit]

    @property
    def is_ready(self) -> bool:
        return bool(self._by_symbol)

    @property
    def symbol_count(self) -> int:
        return len(self._by_symbol)


# Global singleton used across the app
instruments_lookup = UpstoxInstrumentsLookup()
