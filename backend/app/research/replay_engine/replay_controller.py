"""
ReplayController — Orchestrates market data replay sessions.

Controls:
  - Replay speed (1x, 2x, 10x, max)
  - Symbol filtering (replay subset of instruments)
  - Session management (start/stop/pause)
  - Deterministic execution through real pipeline

Usage:
    controller = ReplayController()
    await controller.start_session(
        start_date="2025-06-01",
        end_date="2025-06-30",
        symbols=["RELIANCE", "TCS"],
        speed=2.0,
    )
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import date
from typing import Optional

from app.core.logging import logger


class ReplaySession:
    """Tracks state for a single replay session."""

    def __init__(
        self,
        session_id: str,
        start_date: date,
        end_date: date,
        symbols: list[str],
        speed: float = 1.0,
    ):
        self.session_id = session_id
        self.start_date = start_date
        self.end_date = end_date
        self.symbols = symbols
        self.speed = speed
        self.is_running = False
        self.is_paused = False
        self.candles_published = 0
        self.progress_pct = 0.0


class ReplayController:
    """Orchestrate replay sessions with speed control and session management."""

    def __init__(self):
        self._sessions: dict[str, ReplaySession] = {}
        self._current_session: Optional[str] = None
        self._replay_task: Optional[asyncio.Task] = None

    async def start_session(
        self,
        start_date: str,
        end_date: str,
        symbols: list[str] | None = None,
        speed: float = 1.0,
    ) -> str:
        """
        Start a new replay session.

        Args:
            start_date: Start date (YYYY-MM-DD)
            end_date: End date (YYYY-MM-DD)
            symbols: List of symbols to replay (None = all)
            speed: Replay speed multiplier (1.0 = real-time)

        Returns:
            Session ID string
        """
        session_id = str(uuid.uuid4())[:8]
        sd = date.fromisoformat(start_date)
        ed = date.fromisoformat(end_date)

        session = ReplaySession(
            session_id=session_id,
            start_date=sd,
            end_date=ed,
            symbols=symbols or [],
            speed=speed,
        )

        self._sessions[session_id] = session
        self._current_session = session_id
        session.is_running = True

        # Start replay in background
        from app.research.replay_engine.replay_publisher import ReplayPublisher
        publisher = ReplayPublisher()
        self._replay_task = asyncio.create_task(
            publisher.publish_historical(session)
        )

        logger.info(
            f"ReplayController: Started session {session_id} "
            f"({start_date} → {end_date}, speed={speed}x, "
            f"symbols={len(session.symbols) or 'ALL'})"
        )
        return session_id

    async def stop_session(self, session_id: str) -> None:
        """Stop a running replay session."""
        session = self._sessions.get(session_id)
        if session:
            session.is_running = False
            if self._replay_task and not self._replay_task.done():
                self._replay_task.cancel()
            logger.info(f"ReplayController: Stopped session {session_id}")

    async def pause_session(self, session_id: str) -> None:
        """Pause a running replay session."""
        session = self._sessions.get(session_id)
        if session:
            session.is_paused = True
            logger.info(f"ReplayController: Paused session {session_id}")

    async def resume_session(self, session_id: str) -> None:
        """Resume a paused replay session."""
        session = self._sessions.get(session_id)
        if session:
            session.is_paused = False
            logger.info(f"ReplayController: Resumed session {session_id}")

    def get_session_status(self, session_id: str) -> dict | None:
        """Get status of a replay session."""
        session = self._sessions.get(session_id)
        if not session:
            return None
        return {
            "session_id": session.session_id,
            "is_running": session.is_running,
            "is_paused": session.is_paused,
            "candles_published": session.candles_published,
            "progress_pct": round(session.progress_pct, 1),
            "speed": session.speed,
        }
