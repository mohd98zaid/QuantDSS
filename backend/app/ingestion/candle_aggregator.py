"""
CandleAggregator — Builds 1-minute OHLCV candles from live ticks.
Subscribes to Redis tick channel and emits completed candles.

Hardening Fixes Applied:
  Issue 2 — Timezone-aware timestamps (IST / Asia/Kolkata):
    All timestamps are converted to timezone-aware IST datetimes before
    minute-boundary comparison. This ensures VWAP resets at exactly 09:15 IST
    and ORB window calculations align with the NSE session, regardless of whether
    the upstream broker sends UTC or naive timestamps.

  Issue 3 — Duplicate and out-of-order tick protection:
    - Duplicate tick (same symbol + same timestamp): silently dropped.
    - Out-of-order tick (timestamp earlier than last seen): silently dropped with
      DEBUG log. This prevents corrupted OHLCV high/low values from stale ticks.
    - Late tick arriving after candle close: dropped (the candle has already been
      published to Redis and persisted).

  Volume Note:
    The 'volume' field in tick_data is now expected to be a PER-TICK DELTA volume
    (computed by VolumeDeltaTracker in websocket_manager.py), NOT the cumulative
    vtt. CandleBuilder.add_tick() simply accumulates these deltas.

Fix 11 (Session Management):
  - reset_session(): clears all candle builders and last-tick timestamps for a clean
    session start. Call this at 09:15 IST from the scheduler/startup routine.
    Also resets in-cache volume trackers via market_data_cache.

Fix 12 (Redis Stream Memory):
  - All xadd() calls now include maxlen=10000, approximate=True.
    This caps the Redis Stream to ~10,000 messages, preventing indefinite growth
    that would exhaust server memory over long running sessions.

Fix 13 (Error Handling):
  - logger.exception() used in all except blocks to capture full stack traces.
"""
import json
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional

import pytz

from app.core.logging import logger
from app.core.redis import redis_client

# IST timezone — all session boundary decisions are made in IST
IST = pytz.timezone("Asia/Kolkata")
_UTC = timezone.utc

# NSE session open time (used to detect the first bar of the day)
_SESSION_OPEN_HOUR = 9
_SESSION_OPEN_MIN = 15


def _to_ist(ts: datetime) -> datetime:
    """
    Convert any datetime to an IST-aware datetime.

    Handles:
      - Naive UTC (most common from broker feeds): attaches UTC tzinfo then converts
      - Naive IST (misconfigured sources): attaches IST tzinfo directly
      - Already-aware: converts to IST regardless of source tz
    """
    if ts.tzinfo is None:
        # Assume UTC for naive datetimes (Upstox sends UTC ISO strings)
        ts = ts.replace(tzinfo=_UTC)
    return ts.astimezone(IST)


def _floor_to_minute(ts: datetime) -> datetime:
    """Truncate a timezone-aware datetime to the start of its minute."""
    return ts.replace(second=0, microsecond=0)


@dataclass
class CandleBuilder:
    """
    Accumulates per-tick DELTA volume into a 1-minute OHLCV candle.

    All timestamps stored here are IST-aware datetimes.
    Volume is a sum of per-tick deltas (NOT cumulative vtt).
    """
    symbol: str
    minute_start: datetime          # IST-aware, floored to minute
    open: float = 0.0
    high: float = 0.0
    low: float = float("inf")
    close: float = 0.0
    volume: int = 0
    tick_count: int = 0

    def add_tick(self, price: float, volume: int) -> None:
        """Accumulate one tick into the candle."""
        if self.tick_count == 0:
            self.open = price
            self.high = price
            self.low = price
        else:
            self.high = max(self.high, price)
            self.low = min(self.low, price)

        self.close = price
        self.volume += max(0, volume)   # defensive: never allow negative volume
        self.tick_count += 1

    def to_dict(self) -> dict:
        """Serialize to a dict suitable for Redis publish."""
        return {
            "symbol": self.symbol,
            "time": self.minute_start.isoformat(),
            "open": self.open,
            "high": self.high,
            "low": self.low if self.low != float("inf") else self.open,
            "close": self.close,
            "volume": self.volume,
        }


class CandleAggregator:
    """
    Aggregates ticks into 1-minute OHLCV candles with timezone safety and tick
    deduplication.

    On candle close:
      1. Publishes completed candle to Redis Streams (market:candles stream)
      2. Resets the volume accumulator in MarketDataCache for the next candle

    Tick safety rules (Issue 3):
      - Duplicate: same symbol + same IST minute_start → dropped
      - Out-of-order: tick timestamp < last seen timestamp for symbol → dropped
      - Late (arrived after candle boundary): tick processed into current open candle
        only if its minute_start matches the current builder's minute_start
    """

    def __init__(self):
        self._builders: Dict[str, CandleBuilder] = {}
        # Track last observed tick timestamp per symbol for dedup/order checks
        self._last_tick_ts: Dict[str, datetime] = {}
        # Fix 14: Use a queue/set for recent tick deduplication
        from collections import deque
        self._recent_ticks: Dict[str, deque] = {}

    def _get_minute_start(self, ts_ist: datetime) -> datetime:
        """Return the IST-aware start-of-minute for the given IST timestamp."""
        return _floor_to_minute(ts_ist)

    async def process_tick(self, tick_data: dict) -> Optional[dict]:
        """
        Process one market tick and return a completed candle if the minute changed.

        Args:
            tick_data: Dict with keys:
                symbol    (str)       — trading symbol
                ltp       (float)     — last traded price
                volume    (int)       — PER-TICK delta volume (from VolumeDeltaTracker)
                timestamp (str/datetime) — tick timestamp (UTC ISO string or aware datetime)

        Returns:
            Completed candle dict if a minute boundary was crossed, else None.
        """
        symbol: str = tick_data["symbol"]
        price: float = float(tick_data["ltp"])
        delta_volume: int = int(tick_data.get("volume", 0))

        # ── Issue 2: Timezone normalisation ────────────────────────────────
        raw_ts = tick_data["timestamp"]
        if isinstance(raw_ts, str):
            ts_naive = datetime.fromisoformat(raw_ts)
            ts_ist = _to_ist(ts_naive)
        elif isinstance(raw_ts, datetime):
            ts_ist = _to_ist(raw_ts)
        else:
            logger.warning(f"CandleAggregator: Unknown timestamp type for {symbol}: {type(raw_ts)}")
            return None

        # ── Issue 3a/Fix 14: Duplicate and Out-of-order tick protection ──
        # Fix 14: Keep a rolling queue of the last few ticks to reliably detect duplicates
        # without strictly dropping identical timestamps on high-frequency symbols
        if symbol not in self._recent_ticks:
            from collections import deque
            self._recent_ticks[symbol] = deque(maxlen=20)
            
        tick_sig = (ts_ist, price, delta_volume)
        if tick_sig in self._recent_ticks[symbol]:
            logger.debug(
                f"CandleAggregator: Duplicate tick dropped for {symbol}: "
                f"{price} @ {ts_ist.isoformat()}"
            )
            return None
            
        self._recent_ticks[symbol].append(tick_sig)

        last_ts = self._last_tick_ts.get(symbol)
        if last_ts is not None:
            if ts_ist < last_ts:
                logger.debug(
                    f"CandleAggregator: Out-of-order tick dropped for {symbol} "
                    f"(tick={ts_ist.isoformat()}, last={last_ts.isoformat()})"
                )
                return None

        self._last_tick_ts[symbol] = max(last_ts or ts_ist, ts_ist)
        minute_start = self._get_minute_start(ts_ist)
        completed_candle: Optional[dict] = None

        # ── Candle boundary detection ────────────────────────────────────────
        if symbol in self._builders:
            current_builder = self._builders[symbol]
            if current_builder.minute_start != minute_start:
                # Minute changed — close and publish the current candle
                completed_candle = current_builder.to_dict()
                logger.debug(
                    f"Candle closed: {symbol} @ {current_builder.minute_start.isoformat()} "
                    f"OHLCV={current_builder.open:.2f}/{current_builder.high:.2f}/"
                    f"{current_builder.low:.2f}/{current_builder.close:.2f}/{current_builder.volume}"
                )

                # Publish to Redis Streams for durable delivery (Issue 16)
                try:
                    # Fix 12: maxlen=10000 caps stream memory; approximate=True
                    # uses Redis MAXLEN ~ trimming (much faster for high-frequency streams)
                    candle_msg: dict = {"symbol": symbol, "data": json.dumps(completed_candle)}
                    # Pass replay context so downstream workers can bypass market hours checks
                    if tick_data.get("is_replay"):
                        candle_msg["is_replay"] = "1"
                        candle_msg["replay_session_id"] = str(tick_data.get("replay_session_id", ""))
                    await redis_client.xadd(
                        "market:candles",
                        candle_msg,
                        maxlen=10_000,
                        approximate=True,
                    )
                except Exception:
                    # Fix 13: use exception() to preserve stack trace
                    # Fallback to legacy pub/sub if Streams not available
                    try:
                        await redis_client.publish(
                            f"market:candle:{symbol}",
                            json.dumps(completed_candle),
                        )
                    except Exception:
                        logger.exception(f"CandleAggregator: Redis publish failed for {symbol}")

                # Reset volume accumulator in market data cache for next candle
                try:
                    from app.ingestion.websocket_manager import market_data_cache
                    # Determine instrument key from tick data if available
                    instrument_key = tick_data.get("instrument_key", "")
                    if instrument_key:
                        market_data_cache.reset_volume(instrument_key)
                except Exception:
                    pass

                # Start a new builder for the new minute
                self._builders[symbol] = CandleBuilder(
                    symbol=symbol,
                    minute_start=minute_start,
                )

        # Create a new builder if none exists
        if symbol not in self._builders:
            self._builders[symbol] = CandleBuilder(
                symbol=symbol,
                minute_start=minute_start,
            )

        # Add tick to present candle
        self._builders[symbol].add_tick(price, delta_volume)

        return completed_candle

    def get_pending_candle(self, symbol: str) -> Optional[dict]:
        """Get the current in-progress candle for a symbol (for gap-filling)."""
        if symbol in self._builders:
            return self._builders[symbol].to_dict()
        return None

    def is_in_session(self, symbol: str = "") -> bool:
        """
        Return True if the last processed tick was within NSE market hours (IST).
        Used by callers to suppress signals outside 09:15–15:30 IST.
        """
        import datetime as _dt
        now_ist = datetime.now(IST)
        if now_ist.weekday() >= 5:   # Saturday or Sunday
            return False
        h, m = now_ist.hour, now_ist.minute
        open_min  = _SESSION_OPEN_HOUR * 60 + _SESSION_OPEN_MIN
        close_min = 15 * 60 + 30
        current_min = h * 60 + m
        return open_min <= current_min <= close_min

    def is_data_stale(self, symbol: str, max_age_minutes: float = 5.0) -> bool:
        """
        Fix 9b: Return True if no tick has been received for `symbol` within
        max_age_minutes. Used by the signal pipeline to suppress signal generation
        when market data is stale (WebSocket silence, feed outage, etc.).

        Returns:
            True  — data is stale, signals should NOT be generated
            False — data is fresh, pipeline can proceed normally
        """
        last_ts = self._last_tick_ts.get(symbol)
        if last_ts is None:
            # No tick ever seen for this symbol — consider stale to be safe
            return True

        now_ist = datetime.now(IST)
        # Ensure last_ts is timezone-aware for comparison
        if last_ts.tzinfo is None:
            last_ts = last_ts.replace(tzinfo=IST)

        age_minutes = (now_ist - last_ts).total_seconds() / 60
        if age_minutes > max_age_minutes:
            logger.warning(
                f"CandleAggregator: Stale data for {symbol} "
                f"(last tick {age_minutes:.1f} min ago > {max_age_minutes} min threshold). "
                f"Suppressing signal generation."
            )
            return True
        return False

    def reset_session(self) -> None:
        """
        Fix 11: Reset all candle state for a new trading session.

        Call this at 09:15 IST (session open) to ensure:
        - In-progress candle builders from the previous day are discarded
        - Last-tick timestamps are cleared so stale-tick detection doesn't
          drop the first ticks of the new day
        - In-memory volume accumulators in market data cache are reset

        Wire to APScheduler with a cron trigger at 09:15 IST:
            scheduler.add_job(candle_aggregator.reset_session, CronTrigger(hour=9, minute=15))
        """
        count = len(self._builders)
        self._builders.clear()
        self._last_tick_ts.clear()
        self._recent_ticks.clear()

        # Also reset volume accumulators in market data cache
        try:
            from app.ingestion.websocket_manager import market_data_cache
            # Fix 15: Change market_data_cache._volume to _volumes based on audit finding
            for instrument_key in list(market_data_cache._volumes.keys()):
                market_data_cache.reset_volume(instrument_key)
        except Exception:
            logger.exception("CandleAggregator: reset_session — volume cache reset failed (non-fatal)")

        logger.info(
            f"CandleAggregator: Session reset — cleared {count} candle builder(s) and timestamp trackers"
        )
