"""
Time Filter Layer — Phase 8 of Signal Intelligence System.

Filters signals based on the time of day to enforce trading windows:
- 09:15–09:25: Ignore signals (opening volatility)
- 09:25–10:30: Primary window (all signals allowed)
- 10:30–13:30: Selective window (only high confidence signals allowed)
- 13:30–14:45: Limited trades (score >= 80)
- After 14:45: Reject all signals

Fix C-03:
  - Replaced signal.dominant_direction → signal.signal_type
  - Replaced signal.candle_time → signal.first_timestamp
  - Fixed hasattr check so time filter actually blocks signals
"""

from typing import Callable, Awaitable
from datetime import datetime, time, timezone, timedelta

from app.core.logging import logger
from app.engine.consolidation_layer import ConsolidatedSignal
from app.engine.signal_trace import SignalTracer


class TimeFilterLayer:
    """
    Time Window Filter Layer.
    Filters signals based on time of day.
    """

    def __init__(self):
        self._next_callback: Callable[[ConsolidatedSignal], Awaitable[None]] | None = None

        # Time windows (Indian Standard Time)
        self.ignore_until = time(9, 25)
        self.primary_until = time(10, 30)
        self.selective_until = time(13, 30)
        self.limited_until = time(14, 45)

    def set_callback(self, callback: Callable[[ConsolidatedSignal], Awaitable[None]]):
        """Set the next layer in the pipeline."""
        self._next_callback = callback

    async def evaluate(self, signal: ConsolidatedSignal):
        """Evaluate the signal against time windows."""

        # Fix C-03: Use first_timestamp (set by ConsolidatedSignal), not candle_time
        sig_time = getattr(signal, "first_timestamp", None)
        if sig_time is None:
            # Fallback: use last_timestamp or current time
            sig_time = getattr(signal, "last_timestamp", None)
        if sig_time is None:
            logger.warning("TimeFilter: No timestamp on signal, using current time.")
            IST = timezone(timedelta(hours=5, minutes=30))
            sig_time = datetime.now(IST)

        # Convert to IST if needed
        if isinstance(sig_time, datetime):
            # Fix Group 7: Timezone Consistency
            if sig_time.tzinfo is None:
                sig_time = sig_time.replace(tzinfo=timezone.utc)
            IST = timezone(timedelta(hours=5, minutes=30))
            sig_time = sig_time.astimezone(IST)
            t = sig_time.time()
        else:
            logger.warning(f"TimeFilter: timestamp is not a datetime object: {type(sig_time)}")
            if self._next_callback:
                await self._next_callback(signal)
            return

        # Fix C-03: Use signal.signal_type, not signal.dominant_direction
        direction = signal.signal_type
        sym_name = getattr(signal, "symbol_name", "?")

        # Apply Rules
        # 1. Before 09:25: Ignore (opening volatility)
        if t < self.ignore_until:
            logger.info(f"TimeFilter BLOCKED {sym_name} ({direction}): Before 09:25 ({t})")
            trace_id = getattr(signal, "_trace_id", "")
            SignalTracer.trace_drop(trace_id, "TIME_FILTER", sym_name, f"Before 09:25 ({t})")
            return

        # 2. After 14:45: Reject all
        if t >= self.limited_until:
            logger.info(f"TimeFilter BLOCKED {sym_name} ({direction}): After 14:45 ({t})")
            trace_id = getattr(signal, "_trace_id", "")
            SignalTracer.trace_drop(trace_id, "TIME_FILTER", sym_name, f"After 14:45 ({t})")
            return

        # 3. 13:30 - 14:45: Limited trades (requires high confidence >= 80)
        if t >= self.selective_until and t < self.limited_until:
            score = getattr(signal, "quality_score", 0.0) or 0.0
            if score < 80.0:
                logger.info(f"TimeFilter BLOCKED {sym_name} ({direction}): Limited Window ({t}), Score {score} < 80")
                return

        # 4. 10:30 - 13:30: Selective window (requires medium/high confidence >= 65)
        elif t >= self.primary_until and t < self.selective_until:
            score = getattr(signal, "quality_score", 0.0) or 0.0
            if score < 65.0:
                logger.info(f"TimeFilter BLOCKED {sym_name} ({direction}): Selective Window ({t}), Score {score} < 65")
                return

        # Passed all checks, forward to next layer
        logger.debug(f"TimeFilter PASSED {sym_name} ({direction}) at time {t}")
        trace_id = getattr(signal, "_trace_id", "")
        SignalTracer.trace_pass(trace_id, "TIME_FILTER", sym_name, f"time={t}")
        if self._next_callback:
            await self._next_callback(signal)

time_filter_layer = TimeFilterLayer()
