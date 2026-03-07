"""
BrokerManager — Handles primary/backup broker selection and auto-recovery.

Bug fixes vs original:
  1. Module-level _recovery_task_ref prevents Python GC from silently
     collecting the asyncio.Task object while it's waiting in asyncio.sleep().
  2. Exception handling is now INSIDE the while-loop so a single network
     blip/exception no longer kills the entire recovery loop.
  3. Recovery interval reduced from 300s → 60s for faster failback.
  4. _on_recovery_task_done callback self-restarts if the task crashes at
     the top level (edge case safety net).
"""
import asyncio
from datetime import UTC, datetime

from app.core.config import settings
from app.core.logging import logger
from app.ingestion.adapters.angel_adapter import AngelOneAdapter
from app.ingestion.adapters.shoonya_adapter import ShoonyaAdapter
from app.ingestion.adapters.upstox_adapter import UpstoxAdapter
from app.ingestion.broker_adapter import BrokerAdapter

# ── Strong module-level reference so Python's GC never collects the
#    background asyncio.Task while it is suspended in asyncio.sleep().
_recovery_task_ref: asyncio.Task | None = None


class BrokerManager:
    """Manages broker connections with primary/backup fallback and auto-recovery."""

    RECOVERY_INTERVAL_SECS: int = 60   # check every 60 s (was 300 — too slow)

    def __init__(self):
        self.primary_name = settings.primary_broker.lower()
        self.backup_name  = settings.backup_broker.lower()
        self.active_broker: BrokerAdapter | None = None
        self._recovery_task: asyncio.Task | None = None

        # Instantiate available adapters
        self.adapters: dict[str, BrokerAdapter] = {
            "upstox":  UpstoxAdapter(),
            "shoonya": ShoonyaAdapter(),
            "angel":   AngelOneAdapter(),
        }

    def get_active_broker(self) -> BrokerAdapter | None:
        """Returns the currently active broker instance."""
        return self.active_broker

    async def initialize_session(self) -> bool:
        """
        Attempts to connect to the primary broker.
        Falls back to the backup broker if primary fails, then starts
        the auto-recovery background task.
        Returns True if ANY broker connected successfully.
        """
        logger.info(
            f"BrokerManager: Initializing session. "
            f"Primary={self.primary_name}, Backup={self.backup_name}"
        )

        primary = self.adapters.get(self.primary_name)
        backup  = self.adapters.get(self.backup_name)

        if not primary and not backup:
            logger.error("BrokerManager: No adapters configured!")
            return False

        # ── Try Primary
        if primary:
            logger.info(f"BrokerManager: Connecting primary ({self.primary_name})...")
            if await primary.connect():
                self.active_broker = primary
                self._cancel_recovery_task()
                logger.info(f"BrokerManager: Primary ({self.primary_name}) connected ✓")
                return True
            logger.warning(f"BrokerManager: Primary ({self.primary_name}) failed.")

        # ── Try Backup
        if backup:
            logger.info(f"BrokerManager: Falling back to backup ({self.backup_name})...")
            if await backup.connect():
                self.active_broker = backup
                logger.info(f"BrokerManager: Backup ({self.backup_name}) connected ✓")
                if primary:
                    self._start_recovery_task()
                return True
            logger.error(f"BrokerManager: Backup ({self.backup_name}) also failed.")

        return False

    # ── Recovery Task Lifecycle ──────────────────────────────────────────────

    def _start_recovery_task(self) -> None:
        """Starts the background primary-health monitor task."""
        global _recovery_task_ref
        self._cancel_recovery_task()

        task = asyncio.create_task(
            self._monitor_primary_health(),
            name=f"broker_recovery_{self.primary_name}",
        )
        # Store in two places: self (logical owner) + module global (prevents GC)
        self._recovery_task = task
        _recovery_task_ref  = task
        task.add_done_callback(self._on_recovery_task_done)

        logger.info(
            f"BrokerManager: Recovery monitor started — "
            f"will check {self.primary_name} every {self.RECOVERY_INTERVAL_SECS}s"
        )

    def _cancel_recovery_task(self) -> None:
        """Cancels the running recovery task if one exists."""
        global _recovery_task_ref
        if self._recovery_task and not self._recovery_task.done():
            self._recovery_task.cancel()
        self._recovery_task = None
        _recovery_task_ref  = None

    def _on_recovery_task_done(self, task: asyncio.Task) -> None:
        """
        Callback fired when the recovery task exits for any reason.
        Logs the outcome; self-restarts if the task crashed unexpectedly.
        """
        global _recovery_task_ref
        _recovery_task_ref = None

        if task.cancelled():
            logger.info("BrokerManager: Recovery task cancelled (shutdown or primary restored).")
            return

        exc = task.exception()
        if exc:
            logger.error(
                f"BrokerManager: Recovery task crashed: {exc}. "
                "Restarting in 30 s..."
            )
            # Self-restart so recovery never permanently dies
            try:
                loop = asyncio.get_event_loop()
                loop.call_later(30, self._start_recovery_task)
            except RuntimeError:
                pass   # event loop already closed (app shutdown)

    # ── Background Loop ──────────────────────────────────────────────────────

    async def _monitor_primary_health(self) -> None:
        """
        Periodically tries to reconnect the primary broker.
        Hot-swaps back to primary on success and self-terminates.

        Key design points:
          • asyncio.sleep() runs FIRST so we don't hammer Upstox
            milliseconds after it just failed.
          • Exception handling is INSIDE the while loop so a single
            network error doesn't kill the entire recovery loop.
          • CancelledError is re-raised so the task terminates cleanly
            on app shutdown.
        """
        primary = self.adapters.get(self.primary_name)
        if not primary:
            return

        while True:
            # ── Wait before next attempt
            await asyncio.sleep(self.RECOVERY_INTERVAL_SECS)

            logger.info(
                f"BrokerManager: Recovery check — "
                f"pinging {self.primary_name}..."
            )

            try:
                success = await primary.connect()
            except asyncio.CancelledError:
                logger.info("BrokerManager: Recovery task cancelled cleanly.")
                raise   # propagate so the task is marked cancelled
            except Exception as exc:
                # ── Bug fix: catch here, INSIDE loop — just log and continue
                logger.error(
                    f"BrokerManager: Recovery ping raised {type(exc).__name__}: {exc} "
                    f"— retrying in {self.RECOVERY_INTERVAL_SECS}s"
                )
                continue   # ← don't break the loop

            if success:
                logger.info(
                    f"BrokerManager: ✅ RECOVERY SUCCESS — "
                    f"{self.primary_name} is back online. Hot-swapping..."
                )
                old_backup = self.active_broker

                # Atomic hot-swap
                self.active_broker = primary

                # Disconnect backup gracefully
                if old_backup and old_backup is not primary:
                    try:
                        await old_backup.disconnect()
                        logger.info(
                            f"BrokerManager: Backup ({old_backup.name}) disconnected gracefully."
                        )
                    except Exception as exc:
                        logger.warning(f"BrokerManager: Backup disconnect error: {exc}")

                logger.info("BrokerManager: Hot-swap complete — primary is now active.")
                return   # Self-terminate; callback will clear the ref

            logger.debug(
                f"BrokerManager: {self.primary_name} still down — "
                f"retrying in {self.RECOVERY_INTERVAL_SECS}s"
            )


# Global singleton
broker_manager = BrokerManager()
