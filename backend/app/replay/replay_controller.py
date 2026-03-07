"""
Replay Controller — Manages Market Replay Engine Lifecycle.

Ensures Replay is only allowed when TradingMode is PAPER and Live Trading is disabled.
"""
from typing import Optional

from app.core.logging import logger
from app.engine.trading_mode import trading_mode_controller, TradingMode
from app.replay.market_replay_engine import market_replay_engine


class ReplayController:
    """Controls and validates access to the Market Replay Engine."""

    @classmethod
    async def check_safety_rules(cls) -> None:
        """
        Enforce safety checks. Replay is strictly forbidden if the system is LIVE.
        """
        from app.core.database import async_session_factory
        from app.models.auto_trade_config import AutoTradeConfig
        from sqlalchemy import select
        
        async with async_session_factory() as db:
            result = await db.execute(select(AutoTradeConfig).limit(1))
            cfg = result.scalar_one_or_none()
            
        current_mode = trading_mode_controller.get_mode(cfg)
        
        if current_mode == TradingMode.LIVE:
            raise RuntimeError("SAFETY ABORT: Market Replay cannot run while Trading Mode is LIVE.")
        elif current_mode == TradingMode.DISABLED:
            raise RuntimeError("SAFETY ABORT: Market Replay requires Trading Mode to be PAPER, but it is DISABLED.")

    @classmethod
    async def start(cls, csv_data: str, speed: int = 1) -> str:
        """Starts a replay session if safety rules pass."""
        await cls.check_safety_rules()
        logger.info("ReplayController: Authorized session start")
        return market_replay_engine.start_replay(csv_data, speed)

    @classmethod
    def pause(cls) -> None:
        """Pauses current session."""
        market_replay_engine.pause_replay()

    @classmethod
    async def resume(cls) -> None:
        """Resumes current session."""
        await cls.check_safety_rules() # verify someone didn't turn on LIVE while paused
        market_replay_engine.resume_replay()

    @classmethod
    def stop(cls) -> dict:
        """Stops current session and retrieves metrics."""
        return market_replay_engine.stop_replay()

    @classmethod
    def status(cls) -> dict:
        """Gets current replay status."""
        return market_replay_engine.get_status()


# Optional: Function to fetch replay metrics from the DB directly after run finishes
async def fetch_replay_summary(session_id: str) -> dict:
    """Fetches full generated stats (signals, paper trades) for a given replay_session_id."""
    from app.core.database import async_session_factory
    from app.models.paper_trade import PaperTrade
    from app.models.signal import Signal
    from sqlalchemy import select, func
    
    async with async_session_factory() as db:
        # Currently PaperTrade has replay_session_id. Wait, Signal doesn't have it explicitly right now.
        # So we might just get PaperTrade counts in Phase 1
        trades_result = await db.execute(
            select(PaperTrade).where(PaperTrade.replay_session_id == session_id)
        )
        trades = trades_result.scalars().all()
        
        total_pnl = sum(t.realized_pnl for t in trades if t.realized_pnl)
        
        return {
            "replay_session_id": session_id,
            "total_paper_trades": len(trades),
            "net_pnl": total_pnl,
            "win_rate": sum(1 for t in trades if t.realized_pnl > 0) / max(1, len(trades)),
        }
