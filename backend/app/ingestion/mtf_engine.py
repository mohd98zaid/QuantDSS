"""
Multi-Timeframe Candle Engine — Major Fix 6 (Audit).

Hierarchical candle aggregation:
  Ticks → 1s → 5s → 15s → 30s → 1m → 5m

The existing CandleAggregator already handles tick → 1-minute candles.
This module extends the pipeline by aggregating from raw ticks into
sub-minute timeframes and from 1-minute candles into higher timeframes.

Strategies can request candles by timeframe via get_candles(symbol, timeframe).

All candles are timezone-aware (IST) and reset at market open (09:15 IST).
"""
import collections
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

from app.core.logging import logger

IST = timezone(timedelta(hours=5, minutes=30))

# Supported timeframes ordered by granularity
TIMEFRAMES = ["1s", "5s", "15s", "30s", "1m", "5m"]

# Aggregation hierarchy: child → parent
# 5s is built from 5× 1s candles, 15s from 3× 5s, etc.
AGGREGATION_MAP = {
    "5s":  ("1s",  5),
    "15s": ("5s",  3),
    "30s": ("15s", 2),
    "1m":  ("30s", 2),
    "5m":  ("1m",  5),
}


class MTFCandle:
    """A single OHLCV candle for any timeframe."""
    __slots__ = ("time", "open", "high", "low", "close", "volume", "timeframe", "complete")

    def __init__(self, time: datetime, open_: float, timeframe: str):
        self.time = time
        self.open = open_
        self.high = open_
        self.low = open_
        self.close = open_
        self.volume = 0
        self.timeframe = timeframe
        self.complete = False

    def update(self, price: float, volume: int = 0) -> None:
        """Update candle with a new price and optional volume."""
        self.high = max(self.high, price)
        self.low = min(self.low, price)
        self.close = price
        self.volume += volume

    def merge(self, other: "MTFCandle") -> None:
        """Merge another candle's OHLCV into this one."""
        if self.open == 0:
            self.open = other.open
        self.high = max(self.high, other.high)
        self.low = min(self.low, other.low) if self.low > 0 else other.low
        self.close = other.close
        self.volume += other.volume

    def to_dict(self) -> dict:
        return {
            "time": self.time.isoformat(),
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "timeframe": self.timeframe,
        }


def _floor_time(dt: datetime, seconds: int) -> datetime:
    """Floor a datetime to the nearest timeframe boundary."""
    epoch = dt.timestamp()
    floored = (int(epoch) // seconds) * seconds
    return datetime.fromtimestamp(floored, tz=dt.tzinfo or IST)


def _tf_seconds(tf: str) -> int:
    """Convert timeframe string to seconds."""
    if tf == "1s":
        return 1
    if tf == "5s":
        return 5
    if tf == "15s":
        return 15
    if tf == "30s":
        return 30
    if tf == "1m":
        return 60
    if tf == "5m":
        return 300
    return 60


class MultiTimeframeEngine:
    """
    Manages hierarchical candle aggregation for multiple symbols.

    Usage:
        engine = MultiTimeframeEngine()
        engine.process_tick(symbol, price, volume, timestamp)
        candles = engine.get_candles(symbol, "5m")
    """

    # Max candles to keep per symbol per timeframe
    MAX_HISTORY = 200

    def __init__(self):
        # {symbol: {timeframe: deque[MTFCandle]}}
        self._history: Dict[str, Dict[str, collections.deque]] = {}
        # {symbol: {timeframe: MTFCandle | None}}  — current building candle
        self._current: Dict[str, Dict[str, Optional[MTFCandle]]] = {}

    def _ensure_symbol(self, symbol: str) -> None:
        """Initialize data structures for a new symbol."""
        if symbol not in self._history:
            self._history[symbol] = {
                tf: collections.deque(maxlen=self.MAX_HISTORY)
                for tf in TIMEFRAMES
            }
            self._current[symbol] = {tf: None for tf in TIMEFRAMES}

    def process_tick(
        self,
        symbol: str,
        price: float,
        volume: int,
        timestamp: datetime,
    ) -> List[MTFCandle]:
        """
        Process a raw tick and generate candles for all sub-minute timeframes.

        Returns a list of newly completed candles (any timeframe).
        """
        self._ensure_symbol(symbol)

        # Ensure timezone-aware
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=IST)

        completed: List[MTFCandle] = []

        # 1. Build 1-second candle from tick
        completed.extend(
            self._update_candle(symbol, "1s", price, volume, timestamp)
        )

        # 2. Cascade completions up the hierarchy
        for parent_tf, (child_tf, count) in AGGREGATION_MAP.items():
            child_completed = [c for c in completed if c.timeframe == child_tf]
            for child in child_completed:
                cascaded = self._aggregate_up(symbol, parent_tf, child)
                completed.extend(cascaded)

        return completed

    def process_1m_candle(self, symbol: str, candle: dict) -> List[MTFCandle]:
        """
        Process a completed 1-minute candle (from CandleAggregator) and
        aggregate into 5-minute candles.

        Args:
            symbol: Instrument symbol
            candle: Dict with time, open, high, low, close, volume

        Returns:
            List of newly completed 5-minute candles.
        """
        self._ensure_symbol(symbol)

        candle_time = candle.get("time")
        if isinstance(candle_time, str):
            candle_time = datetime.fromisoformat(candle_time)
        if candle_time and candle_time.tzinfo is None:
            candle_time = candle_time.replace(tzinfo=IST)

        # Store the 1m candle in history
        mtf_1m = MTFCandle(candle_time, float(candle["open"]), "1m")
        mtf_1m.high = float(candle["high"])
        mtf_1m.low = float(candle["low"])
        mtf_1m.close = float(candle["close"])
        mtf_1m.volume = int(candle.get("volume", 0))
        mtf_1m.complete = True
        self._history[symbol]["1m"].append(mtf_1m)

        # Aggregate to 5m
        return self._aggregate_up(symbol, "5m", mtf_1m)

    def _update_candle(
        self,
        symbol: str,
        timeframe: str,
        price: float,
        volume: int,
        timestamp: datetime,
    ) -> List[MTFCandle]:
        """Update or create a candle for the given timeframe. Returns completed candles."""
        completed = []
        tf_sec = _tf_seconds(timeframe)
        candle_start = _floor_time(timestamp, tf_sec)

        current = self._current[symbol][timeframe]

        if current is None or current.time != candle_start:
            # Close the old candle
            if current is not None:
                current.complete = True
                self._history[symbol][timeframe].append(current)
                completed.append(current)

            # Start a new candle
            current = MTFCandle(candle_start, price, timeframe)
            self._current[symbol][timeframe] = current

        current.update(price, volume)
        return completed

    def _aggregate_up(
        self, symbol: str, parent_tf: str, child_candle: MTFCandle
    ) -> List[MTFCandle]:
        """Aggregate a completed child candle into a parent timeframe."""
        completed = []
        parent_sec = _tf_seconds(parent_tf)
        parent_start = _floor_time(child_candle.time, parent_sec)

        current_parent = self._current[symbol][parent_tf]

        if current_parent is None or current_parent.time != parent_start:
            # Close the old parent candle
            if current_parent is not None:
                current_parent.complete = True
                self._history[symbol][parent_tf].append(current_parent)
                completed.append(current_parent)

            # Start a new parent candle
            current_parent = MTFCandle(parent_start, child_candle.open, parent_tf)
            self._current[symbol][parent_tf] = current_parent

        current_parent.merge(child_candle)
        return completed

    def get_candles(self, symbol: str, timeframe: str) -> List[dict]:
        """Get historical candles for a symbol and timeframe."""
        if symbol not in self._history:
            return []
        history = self._history[symbol].get(timeframe)
        if not history:
            return []
        return [c.to_dict() for c in history]

    def get_current_candle(self, symbol: str, timeframe: str) -> Optional[dict]:
        """Get the currently building (incomplete) candle."""
        if symbol not in self._current:
            return None
        current = self._current[symbol].get(timeframe)
        if current is None:
            return None
        return current.to_dict()

    def reset_session(self) -> None:
        """Reset all candle state for a new trading session (09:15 IST)."""
        self._history.clear()
        self._current.clear()
        logger.info("MultiTimeframeEngine: Session reset — all candle state cleared")


# Module-level singleton
mtf_engine = MultiTimeframeEngine()
