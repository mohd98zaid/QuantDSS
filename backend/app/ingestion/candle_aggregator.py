"""
CandleAggregator — Builds 1-minute OHLCV candles from live ticks.
Subscribes to Redis tick channel and emits completed candles.
"""
import json
from dataclasses import dataclass
from datetime import datetime

from app.core.logging import logger
from app.core.redis import publish


@dataclass
class CandleBuilder:
    """Accumulates ticks into a 1-minute OHLCV candle."""
    symbol: str
    minute_start: datetime
    open: float = 0.0
    high: float = 0.0
    low: float = float("inf")
    close: float = 0.0
    volume: int = 0
    tick_count: int = 0

    def add_tick(self, price: float, volume: int):
        if self.tick_count == 0:
            self.open = price
            self.high = price
            self.low = price
        else:
            self.high = max(self.high, price)
            self.low = min(self.low, price)

        self.close = price
        self.volume += volume
        self.tick_count += 1

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "time": self.minute_start.isoformat(),
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
        }


class CandleAggregator:
    """
    Aggregates ticks into 1-minute OHLCV candles.
    On candle close:
      1. Inserts candle into TimescaleDB
      2. Publishes to Redis market:candle:{symbol}
    """

    def __init__(self):
        self._builders: dict[str, CandleBuilder] = {}

    def _get_minute_start(self, timestamp: datetime) -> datetime:
        """Truncate a timestamp to the start of its minute."""
        return timestamp.replace(second=0, microsecond=0)

    async def process_tick(self, tick_data: dict) -> dict | None:
        """
        Process a tick and return a completed candle if the minute has changed.

        Args:
            tick_data: Dict with symbol, ltp, volume, timestamp

        Returns:
            Completed candle dict if a minute boundary was crossed, else None.
        """
        symbol = tick_data["symbol"]
        price = tick_data["ltp"]
        volume = tick_data.get("volume", 0)
        timestamp = datetime.fromisoformat(tick_data["timestamp"])
        minute_start = self._get_minute_start(timestamp)

        completed_candle = None

        # Check if we need to close the current candle
        if symbol in self._builders:
            current_builder = self._builders[symbol]
            if current_builder.minute_start != minute_start:
                # Minute changed — close the current candle
                completed_candle = current_builder.to_dict()
                logger.debug(f"Candle closed: {symbol} @ {current_builder.minute_start}")

                # Publish completed candle to Redis
                await publish(
                    f"market:candle:{symbol}",
                    json.dumps(completed_candle),
                )

                # Start a new builder
                self._builders[symbol] = CandleBuilder(
                    symbol=symbol,
                    minute_start=minute_start,
                )

        if symbol not in self._builders:
            self._builders[symbol] = CandleBuilder(
                symbol=symbol,
                minute_start=minute_start,
            )

        # Add tick to current builder
        self._builders[symbol].add_tick(price, volume)

        return completed_candle

    def get_pending_candle(self, symbol: str) -> dict | None:
        """Get the current in-progress candle for a symbol (for gap-filling)."""
        if symbol in self._builders:
            return self._builders[symbol].to_dict()
        return None
