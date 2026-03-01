"""
BrokerAdapter — Abstract interface for all broker integrations.
All broker implementations must extend this base class.
"""
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime


@dataclass
class NormalisedTick:
    """Normalised tick data from any broker."""
    symbol: str
    ltp: float  # Last traded price
    volume: int
    timestamp: datetime
    exchange: str = "NSE"


class BrokerAdapter(ABC):
    """Abstract base class for broker connections."""

    def __init__(self, name: str):
        self.name = name
        self.is_connected: bool = False
        self._on_tick_callback: Callable | None = None

    @abstractmethod
    async def connect(self) -> bool:
        """
        Authenticate and establish connection.
        Returns True if successful, False otherwise.
        """
        pass

    @abstractmethod
    async def disconnect(self) -> None:
        """Gracefully close the connection."""
        pass

    @abstractmethod
    async def subscribe(self, symbols: list[str]) -> None:
        """
        Subscribe to live tick data for the given symbols.
        Calls the on_tick callback for each received tick.
        """
        pass

    @abstractmethod
    async def unsubscribe(self, symbols: list[str]) -> None:
        """Unsubscribe from tick data for the given symbols."""
        pass

    def set_on_tick(self, callback: Callable[[NormalisedTick], None]) -> None:
        """Register the tick callback function."""
        self._on_tick_callback = callback

    async def on_tick(self, tick: NormalisedTick) -> None:
        """Process an incoming tick — delegates to the registered callback."""
        if self._on_tick_callback:
            await self._on_tick_callback(tick)

    @abstractmethod
    async def get_connection_status(self) -> dict:
        """Return the current connection status for health checks."""
        pass
