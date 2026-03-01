"""
HealthMonitor — WebSocket state machine + reconnection logic.
Monitors broker WebSocket connectivity and alerts trader on failure.
"""
from datetime import UTC, datetime
from enum import Enum

from app.core.logging import logger


class ConnectionState(Enum):
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    RETRYING = "RETRYING"
    RECONNECTING = "RECONNECTING"
    FAILED = "FAILED"


class HealthMonitor:
    """
    Monitors broker WebSocket connection state.

    State Machine:
    [DISCONNECTED] → connect() → [CONNECTING]
    [CONNECTING]   → success   → [CONNECTED]
    [CONNECTING]   → failure   → [RETRYING] (backoff: 1,2,4,8,16s — max 5)
    [RETRYING]     → exceeded  → [FAILED] → Telegram alert
    [CONNECTED]    → on_close  → [RECONNECTING]
    """

    MAX_RETRIES = 5
    BACKOFF_SECONDS = [1, 2, 4, 8, 16]

    def __init__(self):
        self.state: ConnectionState = ConnectionState.DISCONNECTED
        self.retry_count: int = 0
        self.last_tick_at: datetime | None = None
        self.connected_since: datetime | None = None

    def on_connect(self):
        """Called when WebSocket connection is established."""
        self.state = ConnectionState.CONNECTED
        self.retry_count = 0
        self.connected_since = datetime.now(UTC)
        logger.info("HealthMonitor: Broker connected")

    def on_disconnect(self):
        """Called when WebSocket connection is lost."""
        previous_state = self.state
        self.state = ConnectionState.RECONNECTING
        logger.warning(f"HealthMonitor: Broker disconnected (was: {previous_state.value})")

    def on_retry(self) -> bool:
        """
        Called before a reconnection attempt.
        Returns True if retry should proceed, False if max retries exceeded.
        """
        self.retry_count += 1
        if self.retry_count > self.MAX_RETRIES:
            self.state = ConnectionState.FAILED
            logger.error(f"HealthMonitor: Max retries ({self.MAX_RETRIES}) exceeded — FAILED")
            return False

        self.state = ConnectionState.RETRYING
        backoff = self.BACKOFF_SECONDS[min(self.retry_count - 1, len(self.BACKOFF_SECONDS) - 1)]
        logger.warning(f"HealthMonitor: Retry {self.retry_count}/{self.MAX_RETRIES} in {backoff}s")
        return True

    def on_tick_received(self):
        """Called when any tick is received — used to track liveness."""
        self.last_tick_at = datetime.now(UTC)

    def get_status(self) -> dict:
        return {
            "state": self.state.value,
            "retry_count": self.retry_count,
            "last_tick_at": self.last_tick_at.isoformat() if self.last_tick_at else None,
            "connected_since": self.connected_since.isoformat() if self.connected_since else None,
        }
