"""
BrokerManager — Handles primary/backup broker selection and auto-recovery.
"""
import asyncio
from datetime import UTC, datetime

from app.core.config import settings
from app.core.logging import logger
from app.ingestion.adapters.shoonya_adapter import ShoonyaAdapter
from app.ingestion.adapters.upstox_adapter import UpstoxAdapter
from app.ingestion.adapters.angel_adapter import AngelOneAdapter
from app.ingestion.broker_adapter import BrokerAdapter


class BrokerManager:
    """Manages broker connections with primary/backup fallback and auto-recovery."""

    def __init__(self):
        self.primary_name = settings.primary_broker.lower()
        self.backup_name = settings.backup_broker.lower()
        self.active_broker: BrokerAdapter | None = None
        self._recovery_task: asyncio.Task | None = None
        
        # Instantiate available adapters
        self.adapters = {
            "upstox": UpstoxAdapter(),
            "shoonya": ShoonyaAdapter(),
            "angel": AngelOneAdapter(),
        }

    def get_active_broker(self) -> BrokerAdapter | None:
        """Returns the currently active broker instance."""
        return self.active_broker

    async def initialize_session(self) -> bool:
        """
        Attempts to connect to the primary broker.
        If it fails, falls back to the backup broker and starts auto-recovery.
        Returns True if ANY broker was successfully connected.
        """
        logger.info(f"BrokerManager: Initializing session. Primary: {self.primary_name}, Backup: {self.backup_name}")

        primary = self.adapters.get(self.primary_name)
        backup = self.adapters.get(self.backup_name)

        if not primary and not backup:
            logger.error("BrokerManager: Both primary and backup adapters missing!")
            return False

        # Attempt Primary
        if primary:
            logger.info(f"BrokerManager: Attempting primary broker ({self.primary_name})...")
            success = await primary.connect()
            if success:
                self.active_broker = primary
                self._cancel_recovery_task()
                logger.info(f"BrokerManager: Primary broker ({self.primary_name}) connected successfully.")
                return True
            else:
                logger.warning(f"BrokerManager: Primary broker ({self.primary_name}) connection failed.")

        # Fallback to Backup
        if backup:
            logger.info(f"BrokerManager: Falling back to backup broker ({self.backup_name})...")
            success = await backup.connect()
            if success:
                self.active_broker = backup
                logger.info(f"BrokerManager: Backup broker ({self.backup_name}) connected successfully.")
                
                # Start auto-recovery monitor if primary exists
                if primary:
                    self._start_recovery_task()
                    
                return True
            else:
                logger.error(f"BrokerManager: Backup broker ({self.backup_name}) connection also failed.")

        return False

    def _start_recovery_task(self):
        """Starts a background task to monitor primary broker health."""
        self._cancel_recovery_task()
        self._recovery_task = asyncio.create_task(self._monitor_primary_health())
        logger.info(f"BrokerManager: Auto-recovery monitor started for {self.primary_name}")

    def _cancel_recovery_task(self):
        """Cancels the running recovery task if it exists."""
        if self._recovery_task and not self._recovery_task.done():
            self._recovery_task.cancel()
            self._recovery_task = None

    async def _monitor_primary_health(self):
        """
        Background loop: Periodically attempts to reconnect primary broker.
        If successful, hot-swaps active broker back to primary.
        """
        primary = self.adapters.get(self.primary_name)
        if not primary:
            return

        while True:
            await asyncio.sleep(300)  # Check every 5 minutes
            
            logger.info(f"BrokerManager: Background check — Attempting to recover primary ({self.primary_name})...")
            try:
                success = await primary.connect()
                if success:
                    logger.info(f"BrokerManager: RECOVERY SUCCESS! Primary ({self.primary_name}) is back online. Hot-swapping...")
                    
                    # Store old backup to disconnect later
                    old_backup = self.active_broker
                    
                    # Hot-swap
                    self.active_broker = primary
                    
                    # Disconnect old backup gracefully
                    if old_backup:
                        logger.info(f"BrokerManager: Gracefully disconnecting backup ({old_backup.name})...")
                        await old_backup.disconnect()
                        
                    logger.info("BrokerManager: Hot-swap complete. Primary is now active.")
                    
                    # Self-terminate this recovery task
                    return
                else:
                    logger.debug(f"BrokerManager: Background check — Primary ({self.primary_name}) still unavailable.")
            except Exception as e:
                logger.error(f"BrokerManager: Background recovery check error: {e}")

# Global singleton
broker_manager = BrokerManager()
