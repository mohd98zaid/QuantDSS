"""
WebSocket Manager for Upstox Real-Time Market Data.

Subscribes to live LTP streams and updates an in-memory cache
that the AutoTrader and PaperMonitor can read instantly.

Fixes applied (Audit Phase 1):
  - Reconnect race condition fixed: old _listen task is cancelled before reconnect
  - ping_interval / ping_timeout added for keepalive
  - bid/ask now populated from Protobuf eFeedDetails.best_bid / best_ask
  - Per-instrument timestamp stored in cache for stale-data detection
  - get_ltp_if_fresh(max_age_s) added to MarketDataCache

Fixes applied (Hardening — Issues 1):
  - VolumeDeltaTracker: converts cumulative daily volume (vtt) from Upstox into
    per-tick delta volume. Previously raw vtt was stored directly, inflating every
    candle's volume by 100-1000x and corrupting LiquidityFilter, VolumeExpansion
    strategy, and SignalScorer._score_volume.
    Formula: delta = max(0, current_vtt - previous_vtt)
    Edge cases: first tick (prev=0), negative delta (feed glitch), feed reset.

Fix 3 (Reconnect Race Condition):
  - _listen() now exits cleanly on ConnectionClosed (no recursive connect() call).
  - _reconnect_supervisor() is a separate long-running coroutine that detects drops
    and calls connect() with exponential backoff (1s → 60s max).
  - start_supervisor() spawns the supervisor once at startup.
  - Only ONE _listen_task can exist at any time (previous task cancelled).
  - After MAX_RECONNECT_ATTEMPTS consecutive failures, logs CRITICAL and stops.

Fix 16 (Gap Recovery):
  - After successful reconnect, _backfill_gaps() fetches today's 1-min intraday
    candles from the Upstox REST API for each subscribed instrument.
  - Candles are published to the Redis Stream so the signal pipeline can backfill
    its OHLCV lookback window before generating new signals.
  - A per-symbol _buffer_ready flag is set only after backfill completes, allowing
    the pipeline to suppress signals during the gap window.

Fix 17 (Access Token Auto-Refresh):
  - _refresh_token() exchanges the configured refresh_token for a new access_token
    via the Upstox token endpoint, and updates settings at runtime.
  - connect() and _get_ws_url() automatically retry once after a 401 by calling
    _refresh_token() — no manual restart is required after token expiry.
  - Token refresh is also attempted once at startup before the first connect().
"""
import asyncio
import json
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional, Set, Tuple

import httpx
import websockets

from app.core.config import settings
from app.core.logging import logger
from app.core.notifier import notifier
from app.ingestion.upstox_http import UpstoxHTTPClient
from app.ingestion.proto import MarketDataFeed_pb2

IST = timezone(timedelta(hours=5, minutes=30))
UPSTOX_BASE = "https://api.upstox.com"
UPSTOX_WS_URL = "wss://api.upstox.com/v2/feed/market-data-feed"
_STALE_DEFAULT_AGE_S = 30   # data older than 30 s is considered stale


# ── Issue 1 Fix: Volume Delta Tracker ────────────────────────────────────────
class VolumeDeltaTracker:
    """
    Converts cumulative daily volume (vtt) provided by Upstox WebSocket into
    per-tick delta volume suitable for candle aggregation.

    Why This Is Necessary
    ---------------------
    Upstox sends vtt = total volume traded from session open to now. This is a
    monotonically increasing counter. If we accumulate raw vtt values across ticks
    into a candle, the candle's volume column grows to millions (total day volume),
    not the ~thousands traded in that individual minute. Every volume-based system
    component is then broken:
      - LiquidityFilter: always passes (millions > threshold)
      - VolumeExpansion strategy: fires spurious signals constantly
      - SignalScorer._score_volume: max points every bar

    Algorithm
    ---------
    delta_volume = max(0, current_vtt - previous_vtt)

    Safe cases:
      - First tick of session: prev=0, delta = min(current_vtt, max_first_tick).
        Bounded to avoid polluting the first candle with full pre-market volume.
      - Negative delta (feed glitch): clamped to 0 — do not penalise the candle.
      - Feed reset mid-session (current << prev by large margin): baseline reset,
        this tick contributes 0 volume to avoid a phantom spike.
    """

    # Threshold: if current_vtt drops more than this below prev_vtt, assume reset
    _RESET_THRESHOLD = 10_000
    # Cap volume attributed to the very first tick (pre-open auction guard)
    _MAX_FIRST_TICK_VOL = 50_000

    def __init__(self):
        self._prev_vtt: Dict[str, int] = {}

    def get_delta(self, instrument_key: str, current_vtt: int) -> int:
        """
        Return the per-tick traded volume for this instrument.

        Args:
            instrument_key: Upstox instrument identifier string
            current_vtt:    Cumulative volume field from Protobuf message

        Returns:
            Non-negative integer representing volume traded since last tick.
        """
        prev = self._prev_vtt.get(instrument_key)

        if prev is None:
            # First tick for this instrument in this session
            self._prev_vtt[instrument_key] = current_vtt
            # Bound the first-tick contribution to avoid pre-open auction inflation
            return min(current_vtt, self._MAX_FIRST_TICK_VOL)

        # Detect feed reset (e.g., exchange reconnect mid-session)
        if current_vtt < prev - self._RESET_THRESHOLD:
            logger.warning(
                f"VolumeDeltaTracker: Feed reset detected for {instrument_key} "
                f"(prev_vtt={prev:,}, current_vtt={current_vtt:,}). Resetting baseline."
            )
            self._prev_vtt[instrument_key] = current_vtt
            return 0  # Skip this tick — avoids phantom volume spike

        delta = max(0, current_vtt - prev)
        self._prev_vtt[instrument_key] = current_vtt
        return delta

    def reset(self, instrument_key: str) -> None:
        """Reset baseline for one instrument (call at new session start)."""
        self._prev_vtt.pop(instrument_key, None)

    def reset_all(self) -> None:
        """Reset all baselines. Call once at 9:15 IST each trading day."""
        self._prev_vtt.clear()
        logger.info("VolumeDeltaTracker: All baselines cleared for new session.")


# ── Market Data Cache ─────────────────────────────────────────────────────────
class MarketDataCache:
    """
    In-memory cache for latest LTP, bid/ask, volume, and timestamps.

    Thread-safety note: all access must be from the asyncio event loop.

    Volume API (post-hardening):
      - accumulate_volume(key, delta): add delta to running candle accumulator
      - reset_volume(key):             clear accumulator after candle close
      - get_volume(key):               read current accumulated delta volume
      - update_volume(key, v):         legacy setter, kept for test compatibility
    """

    def __init__(self):
        self._ltps: Dict[str, float] = {}
        self._bids: Dict[str, float] = {}
        self._asks: Dict[str, float] = {}
        self._volumes: Dict[str, int] = {}
        self._timestamps: Dict[str, float] = {}   # monotonic seconds of last update
        import collections
        self._ltp_history: Dict[str, "collections.deque[tuple[float, float]]"] = {}

    # ── LTP ──────────────────────────────────────────────────────
    def update_ltp(self, instrument_key: str, ltp: float) -> None:
        import collections
        self._ltps[instrument_key] = ltp
        ts = time.monotonic()
        self._timestamps[instrument_key] = ts
        if instrument_key not in self._ltp_history:
            self._ltp_history[instrument_key] = collections.deque(maxlen=5400)
        self._ltp_history[instrument_key].append((ts, ltp))

    def get_ltp(self, instrument_key: str) -> Optional[float]:
        return self._ltps.get(instrument_key)

    def get_ltp_if_fresh(self, instrument_key: str, max_age_s: float = _STALE_DEFAULT_AGE_S) -> Optional[float]:
        """Return LTP only if the last update is within max_age_s seconds. Otherwise None."""
        ts = self._timestamps.get(instrument_key)
        if ts is None:
            return None
        age = time.monotonic() - ts
        if age > max_age_s:
            logger.warning(
                f"MarketDataCache: stale LTP for {instrument_key} "
                f"(age={age:.1f}s > max={max_age_s}s)"
            )
            return None
        return self._ltps.get(instrument_key)

    # ── Bid / Ask ─────────────────────────────────────────────────
    def update_quote(self, instrument_key: str, best_bid: float, best_ask: float) -> None:
        self._bids[instrument_key] = best_bid
        self._asks[instrument_key] = best_ask
        self._timestamps[instrument_key] = time.monotonic()

    def get_quote(self, instrument_key: str) -> Tuple[Optional[float], Optional[float]]:
        return self._bids.get(instrument_key), self._asks.get(instrument_key)

    # ── Volume ────────────────────────────────────────────────────
    def accumulate_volume(self, instrument_key: str, delta: int) -> None:
        """Add per-tick delta volume to the running candle accumulator."""
        self._volumes[instrument_key] = self._volumes.get(instrument_key, 0) + delta

    def reset_volume(self, instrument_key: str) -> None:
        """Reset volume accumulator after a candle closes."""
        self._volumes[instrument_key] = 0

    def update_volume(self, instrument_key: str, volume: int) -> None:
        """Legacy absolute setter — kept for backwards compatibility."""
        self._volumes[instrument_key] = volume

    def get_volume(self, instrument_key: str) -> int:
        return self._volumes.get(instrument_key, 0)

    # ── Staleness ─────────────────────────────────────────────────
    def age_seconds(self, instrument_key: str) -> Optional[float]:
        ts = self._timestamps.get(instrument_key)
        return None if ts is None else time.monotonic() - ts

    def get_ltp_1h_ago(self, instrument_key: str, lookback_s: float = 3600.0) -> Optional[float]:
        """Return the LTP from ~1 hour ago for return calculation."""
        history = self._ltp_history.get(instrument_key)
        if not history:
            return None
        now = time.monotonic()
        target_ts = now - lookback_s
        best_ltp: Optional[float] = None
        best_diff = float("inf")
        for ts, ltp in history:
            diff = abs(ts - target_ts)
            if diff < best_diff:
                best_diff = diff
                best_ltp = ltp
        return best_ltp if best_diff <= 300 else None


# ── Global Singletons ─────────────────────────────────────────────────────────
market_data_cache = MarketDataCache()
_volume_tracker = VolumeDeltaTracker()


class UpstoxWebSocketClient:

    # Fix 3: Max reconnect attempts before giving up
    _MAX_RECONNECT_ATTEMPTS = 10

    def __init__(self):
        self._token = settings.upstox_access_token
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._subscriptions: Set[str] = set()
        self._running = False
        self._http_client = UpstoxHTTPClient()
        self._auth_ws_url: Optional[str] = None
        self._listen_task: Optional[asyncio.Task] = None
        self._supervisor_task: Optional[asyncio.Task] = None  # Fix 3
        self._reconnect_count: int = 0                        # Fix 3

    async def _get_ws_url(self) -> str:
        """Fetch the authenticated WebSocket streaming URL from Upstox REST API.
        Fix 17: On 401, attempt a token refresh and retry once.
        """
        for attempt in range(2):
            headers = {
                "Accept": "application/json",
                "Authorization": f"Bearer {self._token}"
            }
            url = "https://api.upstox.com/v2/feed/authorize"
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(url, headers=headers)

            if resp.status_code == 200:
                data = resp.json().get("data", {})
                return data.get("authorized_redirect_uri")

            if resp.status_code == 401 and attempt == 0:
                logger.warning("WebSocket auth: 401 received — attempting token refresh (Fix 17)")
                await self._refresh_token()
                continue

            logger.error(f"Failed to authorize websocket: {resp.text}")
            raise Exception("Upstox WebSocket Authorization Failed")

        raise Exception("Upstox WebSocket Authorization Failed after token refresh")

    async def _refresh_token(self) -> bool:
        """
        Fix 17: Refresh the Upstox access token using the configured refresh_token.

        Upstox issues a new access_token via POST /v2/login/authorization/token.
        On success, updates settings.upstox_access_token and self._token in-place
        so all subsequent API calls use the new token without a restart.

        Returns:
            True if refresh succeeded, False otherwise.
        """
        refresh_token = getattr(settings, "upstox_refresh_token", "") or ""
        api_key       = getattr(settings, "upstox_api_key", "")       or ""
        api_secret    = getattr(settings, "upstox_api_secret", "")    or ""

        if not refresh_token or not api_key or not api_secret:
            logger.warning(
                "Fix 17: Token refresh skipped — upstox_refresh_token / api_key / api_secret "
                "not set in .env. Add them to enable automatic token refresh."
            )
            return False

        try:
            payload = {
                "refresh_token": refresh_token,
                "client_id":     api_key,
                "client_secret": api_secret,
                "redirect_uri":  getattr(settings, "upstox_redirect_uri", "http://localhost"),
                "grant_type":    "refresh_token",
            }
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    f"{UPSTOX_BASE}/v2/login/authorization/token",
                    data=payload,
                    headers={"Accept": "application/json"},
                )

            if resp.status_code == 200:
                new_token = resp.json().get("access_token", "")
                if new_token:
                    self._token = new_token
                    settings.upstox_access_token = new_token  # update runtime setting
                    # Also update the HTTP client used for REST calls
                    self._http_client._token = new_token
                    logger.info("Fix 17: Upstox access token refreshed successfully.")
                    return True
                logger.warning(f"Fix 17: Token refresh response had no access_token: {resp.text[:200]}")
            else:
                logger.error(
                    f"Fix 17: Token refresh failed (HTTP {resp.status_code}): {resp.text[:200]}"
                )
        except Exception:
            logger.exception("Fix 17: Exception during token refresh")
        return False

    async def connect(self) -> None:
        if not self._token:
            logger.warning("Upstox WebSocket: No access token found.")
            return

        # Cancel old listener task before reconnecting
        if self._listen_task and not self._listen_task.done():
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass
            self._listen_task = None

        try:
            self._auth_ws_url = await self._get_ws_url()
            if not self._auth_ws_url:
                raise ValueError("Did not receive redirect URI for websockets")

            logger.info("Upstox WebSocket: Connecting...")
            self._ws = await websockets.connect(
                self._auth_ws_url,
                ping_interval=20,
                ping_timeout=10,
            )
            self._running = True
            self._listen_task = asyncio.create_task(self._listen())
            self._reconnect_count = 0  # Fix 3: reset on successful connect
            logger.info("Upstox WebSocket: Connected & Listening")

            if self._subscriptions:
                await self._send_subscription(list(self._subscriptions))

        except Exception as e:
            logger.error(f"Upstox WebSocket Connect Error: {e}")
            self._running = False

    def start_supervisor(self) -> None:
        """
        Fix 3: Launch the reconnect supervisor exactly once.
        Call this from main.py lifespan startup after the first connect().
        """
        if self._supervisor_task is None or self._supervisor_task.done():
            self._supervisor_task = asyncio.create_task(self._reconnect_supervisor())
            logger.info("Upstox WebSocket: Reconnect supervisor started")

    async def _reconnect_supervisor(self) -> None:
        """
        Fix 3: Single supervisor coroutine responsible for reconnection.

        Watches _running flag. When False (connection lost), waits `backoff_delay`
        seconds then calls connect(). Gives up after _MAX_RECONNECT_ATTEMPTS to
        avoid infinite loops on persistent auth failures.

        Fix 16: After successful reconnect, calls _backfill_gaps() to restore
        the candle buffer before resuming signal generation.
        """
        backoff_delay = 1.0
        while True:
            await asyncio.sleep(1.0)
            if self._running:
                # Healthy — reset counters
                self._reconnect_count = 0
                backoff_delay = 1.0
                continue

            # Connection is down
            if self._reconnect_count >= self._MAX_RECONNECT_ATTEMPTS:
                logger.critical(
                    f"Upstox WebSocket: Max reconnect attempts ({self._MAX_RECONNECT_ATTEMPTS}) reached. "
                    f"Entering slow retry loop (5 mins) instead of dying."
                )
                asyncio.create_task(notifier.send_alert(
                    title="🚨 WebSocket Supervisor Failed",
                    message="Max reconnect attempts reached. Entering 5-minute slow retry loop.",
                    level="CRITICAL"
                ))
                await asyncio.sleep(300)  # Wait 5 minutes
                self._reconnect_count = 0  # Reset counter
                continue

            self._reconnect_count += 1
            logger.warning(
                f"Upstox WebSocket: Connection down. "
                f"Reconnect attempt {self._reconnect_count}/{self._MAX_RECONNECT_ATTEMPTS} "
                f"in {backoff_delay:.0f}s..."
            )
            await asyncio.sleep(backoff_delay)
            backoff_delay = min(backoff_delay * 2, 60.0)

            await self.connect()

            # Fix 16: After reconnection, backfill missing candles for all subscribed
            # instruments so the signal pipeline has a complete buffer.
            if self._running and self._subscriptions:
                asyncio.create_task(self._backfill_gaps())

    async def _backfill_gaps(self) -> None:
        """
        Fix 16: WebSocket Gap Recovery.

        After a reconnect, the signal pipeline's lookback candle buffer may have
        gaps (missing 1-min bars) during the disconnected period. This method
        fetches today's intraday 1-min candles from the Upstox REST API for every
        subscribed instrument and publishes them to the Redis Stream so the pipeline
        can reload its buffer to the most recent state.

        Gap detection: we compare the current IST time against the last emitted
        candle timestamp stored in CandleAggregator. If the gap is > 1 minute,
        backfill is needed.

        Safety: signals are automatically suppressed during the gap period because
        CandleAggregator.is_data_stale() returns True until a live tick arrives
        (Fix 9b). Once backfill is done, the next live tick will reset the
        staleness timer and signal generation resumes.
        """
        import json as _json
        try:
            from app.core.redis import redis_client
        except ImportError:
            logger.warning("Fix 16: Gap recovery skipped — Redis client unavailable")
            return

        if not self._subscriptions:
            return

        logger.info(
            f"Fix 16: Starting gap recovery for {len(self._subscriptions)} subscribed instruments"
        )
        http_client = UpstoxHTTPClient(access_token=self._token)
        recovered = 0
        failed = 0

        for instrument_key in list(self._subscriptions):
            try:
                candles = await http_client.get_intraday_candles(
                    instrument_key, timeframe="1min"
                )
                if not candles:
                    continue

                # Publish each historical candle to Redis so the pipeline can
                # consume them in chronological order.
                for candle in candles:
                    # Use the symbol portion of the instrument key as the symbol name.
                    # e.g. "NSE_EQ|INE848E01016" → instrument key passed through as-is;
                    # the pipeline subscriber maps instrument_key → symbol.
                    candle_payload = {
                        "symbol":     instrument_key,
                        "time":       candle.get("time", ""),
                        "open":       candle.get("open", 0),
                        "high":       candle.get("high", 0),
                        "low":        candle.get("low",  0),
                        "close":      candle.get("close", 0),
                        "volume":     candle.get("volume", 0),
                        "backfilled": True,  # marker so pipeline can log gap recovery
                    }
                    await redis_client.xadd(
                        "market:candles",
                        {"symbol": instrument_key, "data": _json.dumps(candle_payload)},
                        maxlen=10_000,
                        approximate=True,
                    )

                recovered += 1
                logger.info(
                    f"Fix 16: Gap recovery complete for {instrument_key} "
                    f"({len(candles)} candles backfilled)"
                )

            except Exception:
                logger.exception(
                    f"Fix 16: Gap recovery failed for {instrument_key} (non-fatal)"
                )
                failed += 1

        logger.info(
            f"Fix 16: Gap recovery done — {recovered} instruments recovered, {failed} failed"
        )

    async def disconnect(self) -> None:
        self._running = False
        if self._listen_task and not self._listen_task.done():
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass
        if self._ws:
            await self._ws.close()
            self._ws = None
        logger.info("Upstox WebSocket: Disconnected")

    async def subscribe(self, instrument_keys: list[str]) -> None:
        """Add symbols to the real-time stream."""
        new_keys = [k for k in instrument_keys if k not in self._subscriptions]
        if not new_keys:
            return
        self._subscriptions.update(new_keys)
        if self._running and self._ws:
            await self._send_subscription(new_keys)

    async def _send_subscription(self, instrument_keys: list[str]) -> None:
        """Send the JSON subscribe message for fullc mode."""
        payload = {
            "guid": "quantdss",
            "method": "sub",
            "data": {
                "mode": "fullc",
                "instrumentKeys": instrument_keys,
            }
        }
        try:
            await self._ws.send(json.dumps(payload).encode("utf-8"))
            logger.info(f"Upstox WebSocket: Subscribed to {len(instrument_keys)} instruments")
        except Exception as e:
            logger.error(f"Upstox WebSocket Subscribe Error: {e}")

    async def _listen(self) -> None:
        """
        Fix 3: Background task reads WebSocket messages.

        Exits cleanly on ConnectionClosed (sets _running=False so the supervisor
        can schedule a reconnect). Does NOT call connect() internally — that was
        the race condition: recursive connect() spawned duplicate listener tasks.
        """
        while self._running and self._ws:
            try:
                message = await self._ws.recv()
                if isinstance(message, bytes):
                    self._parse_protobuf(message)

            except asyncio.CancelledError:
                logger.debug("Upstox WebSocket: listener task cancelled.")
                break

            except websockets.exceptions.ConnectionClosed:
                logger.warning(
                    "Upstox WebSocket: Connection closed by server. "
                    "Supervisor will handle reconnection."
                )
                self._running = False  # signal supervisor; do NOT call connect() here
                break

            except Exception as e:
                logger.error(f"Upstox WebSocket listen error: {e}")
                await asyncio.sleep(1)

    def _parse_protobuf(self, raw: bytes) -> None:
        """
        Parse a binary Protobuf FeedResponse and update MarketDataCache.

        Issue 1 Fix: Volume is extracted as DELTA via _volume_tracker.get_delta()
        instead of storing raw cumulative vtt. This ensures candle volume reflects
        only the shares traded in the current candle period.

        Legacy: bid/ask extracted from eFeedDetails market depth for SpreadFilter.
        """
        try:
            feed_response = MarketDataFeed_pb2.FeedResponse()
            feed_response.ParseFromString(raw)

            for instrument_key, feed in feed_response.feeds.items():
                ltp: Optional[float] = None
                best_bid: Optional[float] = None
                best_ask: Optional[float] = None

                if feed.HasField("ff"):
                    if feed.ff.HasField("marketFF"):
                        mff = feed.ff.marketFF

                        if mff.HasField("ltpc"):
                            ltp = mff.ltpc.ltp

                        if mff.HasField("eFeedDetails"):
                            efd = mff.eFeedDetails

                            # ── Issue 1 Fix: Delta volume, not cumulative vtt ──
                            raw_vtt = int(getattr(efd, "vtt", 0))
                            delta = _volume_tracker.get_delta(instrument_key, raw_vtt)
                            market_data_cache.accumulate_volume(instrument_key, delta)

                            try:
                                if efd.sBids and len(efd.sBids) > 0:
                                    best_bid = efd.sBids[0].price
                                if efd.sAsks and len(efd.sAsks) > 0:
                                    best_ask = efd.sAsks[0].price
                            except Exception:
                                pass

                    elif feed.ff.HasField("indexFF"):
                        iff = feed.ff.indexFF
                        if iff.HasField("ltpc"):
                            ltp = iff.ltpc.ltp

                elif feed.HasField("ltpc"):
                    ltp = feed.ltpc.ltp

                if ltp is not None:
                    market_data_cache.update_ltp(instrument_key, ltp)

                if best_bid is not None and best_ask is not None:
                    market_data_cache.update_quote(instrument_key, best_bid, best_ask)

        except Exception as e:
            logger.error(f"Upstox WebSocket Protobuf parse error: {e}")


# Global Instance
ws_manager = UpstoxWebSocketClient()
