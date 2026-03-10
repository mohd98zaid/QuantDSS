"""
StrategyHealthMonitor — Tracks per-strategy performance and auto-disables
underperforming strategies.

Hardening Fixes (Issue 9):
  - Distributed singleton flaw fixed: Storing all state in Redis rather than local RAM.
  - DB persistence: trade outcomes written to strategy_health_log table on every
    record_trade() call. On startup, hydrate_from_db() replays the last 30 trades.
"""
from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Deque, Optional

from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import logger
from app.core.redis import redis_client

IST = timezone(timedelta(hours=5, minutes=30))

# ── Thresholds ────────────────────────────────────────────────────────────────
WIN_RATE_LOOKBACK      = 20
WIN_RATE_MIN_PCT       = 35.0
PF_LOOKBACK            = 30
PF_MIN                 = 1.0
MAX_CONSECUTIVE_LOSSES = 5

@dataclass
class StrategyMetrics:
    """Snapshot metrics for a single strategy."""
    strategy_id:         int
    win_rate_pct:        float = 0.0
    profit_factor:       float = 0.0
    avg_win:             float = 0.0
    avg_loss:            float = 0.0
    consecutive_losses:  int   = 0
    total_trades:        int   = 0
    is_disabled:         bool  = False
    disable_reason:      Optional[str] = None
    paused_until:        Optional[datetime] = None


@dataclass
class _StrategyState:
    """Internal rolling state for a strategy."""
    trades: Deque[float] = field(default_factory=lambda: deque(maxlen=PF_LOOKBACK))
    consecutive_losses: int = 0
    is_disabled: bool = False
    disable_reason: Optional[str] = None
    paused_until: Optional[datetime] = None


class StrategyHealthMonitor:
    """
    Distributed strategy health tracker backed by Redis and persistent DB.
    """

    # ── Redis Helpers ─────────────────────────────────────────────────────────

    async def _get_state(self, strategy_id: int) -> _StrategyState:
        key = f"strategy_health:{strategy_id}"
        val = await redis_client.get(key)
        if not val:
            return _StrategyState()
        try:
            data = json.loads(val.decode() if isinstance(val, bytes) else val)
            state = _StrategyState()
            state.trades = deque(data.get("trades", []), maxlen=PF_LOOKBACK)
            state.consecutive_losses = data.get("consecutive_losses", 0)
            state.is_disabled = data.get("is_disabled", False)
            state.disable_reason = data.get("disable_reason")
            paused_str = data.get("paused_until")
            state.paused_until = datetime.fromisoformat(paused_str) if paused_str else None
            return state
        except Exception:
            return _StrategyState()

    async def _save_state(self, strategy_id: int, state: _StrategyState) -> None:
        key = f"strategy_health:{strategy_id}"
        data = {
            "trades": list(state.trades),
            "consecutive_losses": state.consecutive_losses,
            "is_disabled": state.is_disabled,
            "disable_reason": state.disable_reason,
            "paused_until": state.paused_until.isoformat() if state.paused_until else None
        }
        await redis_client.set(key, json.dumps(data))

    # ── Public API ─────────────────────────────────────────────────────────────

    async def record_trade(self, strategy_id: int, pnl: float) -> None:
        """
        Record a completed trade P&L for a strategy (in Redis).
        """
        state = await self._get_state(strategy_id)
        state.trades.append(pnl)

        if pnl < 0:
            state.consecutive_losses += 1
        else:
            state.consecutive_losses = 0

        disabled, reason = self._should_disable(state)
        if disabled and not state.is_disabled:
            state.is_disabled = True
            state.disable_reason = reason

            if "CONSECUTIVE" in reason:
                state.paused_until = datetime.now(IST) + timedelta(hours=24)
                logger.warning(
                    f"StrategyHealth: strategy {strategy_id} PAUSED 24h ({reason})"
                )
            else:
                logger.warning(
                    f"StrategyHealth: strategy {strategy_id} DISABLED ({reason})"
                )
        
        await self._save_state(strategy_id, state)

    async def record_trade_async(
        self, strategy_id: int, pnl: float, db: AsyncSession
    ) -> None:
        """
        Record a trade outcome in Redis and persist to DB.
        """
        # 1. Update Redis state
        await self.record_trade(strategy_id, pnl)

        # 2. Persist to DB
        try:
            from app.models.strategy_health_log import StrategyHealthLog
            log_entry = StrategyHealthLog(strategy_id=strategy_id, pnl=pnl)
            db.add(log_entry)
            await db.commit()
        except Exception as e:
            logger.error(f"StrategyHealth: Failed to persist trade log for strategy {strategy_id}: {e}")
            await db.rollback()

    async def hydrate_from_db(self, db: AsyncSession) -> None:
        """
        Replay the last PF_LOOKBACK trade outcomes per strategy
        from the DB to rebuild Redis on startup (in case of Redis wipe).
        """
        try:
            from app.models.strategy_health_log import StrategyHealthLog
            
            result = await db.execute(
                select(StrategyHealthLog.strategy_id).distinct()
            )
            strategy_ids = [row[0] for row in result.fetchall()]

            for sid in strategy_ids:
                result = await db.execute(
                    select(StrategyHealthLog)
                    .where(StrategyHealthLog.strategy_id == sid)
                    .order_by(desc(StrategyHealthLog.recorded_at))
                    .limit(PF_LOOKBACK)
                )
                rows = result.scalars().all()

                # Wipe state in redis and hydrate
                await redis_client.delete(f"strategy_health:{sid}")
                
                # Replay in chronological order (oldest first)
                for row in reversed(rows):
                    await self.record_trade(sid, float(row.pnl))

            logger.info(
                f"StrategyHealth: hydrated {len(strategy_ids)} strategies into Redis"
            )

        except Exception as e:
            logger.warning(f"StrategyHealth: hydrate_from_db failed (non-fatal): {e}")

    async def is_disabled(self, strategy_id: int) -> bool:
        """Return True if this strategy should NOT generate signals right now."""
        state = await self._get_state(strategy_id)
        if not state.is_disabled:
            return False

        # Check if a temporary pause has expired
        if state.paused_until and datetime.now(timezone.utc) > state.paused_until:
            state.is_disabled    = False
            state.disable_reason = None
            state.paused_until   = None
            logger.info(f"StrategyHealth: strategy {strategy_id} pause expired — re-enabled")
            await self._save_state(strategy_id, state)
            return False

        return True

    async def get_metrics(self, strategy_id: int) -> StrategyMetrics:
        """Return the current health snapshot for a strategy."""
        state = await self._get_state(strategy_id)
        trades = list(state.trades)

        wr_trades = trades[-WIN_RATE_LOOKBACK:]
        winners   = [t for t in wr_trades if t > 0]
        losers    = [t for t in wr_trades if t < 0]
        win_rate  = (len(winners) / len(wr_trades) * 100) if wr_trades else 0.0

        pf_trades    = trades[-PF_LOOKBACK:]
        gross_profit = sum(t for t in pf_trades if t > 0)
        gross_loss   = abs(sum(t for t in pf_trades if t < 0))
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else 999.0

        avg_win  = (sum(winners) / len(winners)) if winners else 0.0
        avg_loss = (sum(losers)  / len(losers))  if losers  else 0.0

        return StrategyMetrics(
            strategy_id=strategy_id,
            win_rate_pct=round(win_rate, 1),
            profit_factor=round(profit_factor, 2),
            avg_win=round(avg_win, 2),
            avg_loss=round(avg_loss, 2),
            consecutive_losses=state.consecutive_losses,
            total_trades=len(trades),
            is_disabled=state.is_disabled,
            disable_reason=state.disable_reason,
            paused_until=state.paused_until,
        )

    async def get_all_metrics(self) -> list[StrategyMetrics]:
        """Return health snapshots for all active strategies in Redis."""
        cursor = b"0"
        sids = set()
        while cursor:
            cursor, keys = await redis_client.scan(cursor=cursor, match="strategy_health:*", count=100)
            for k in keys:
                try:
                    sids.add(int(k.decode().split(":")[-1]))
                except ValueError:
                    pass

        metrics = []
        for sid in sids:
            metrics.append(await self.get_metrics(sid))
        return metrics

    async def re_enable(self, strategy_id: int) -> None:
        """Manually re-enable a disabled strategy."""
        state = await self._get_state(strategy_id)
        state.is_disabled    = False
        state.disable_reason = None
        state.paused_until   = None
        logger.info(f"StrategyHealth: strategy {strategy_id} manually re-enabled")
        await self._save_state(strategy_id, state)

    # ── Internal ───────────────────────────────────────────────────────────────

    def _should_disable(self, state: _StrategyState) -> tuple[bool, str]:
        """Evaluate disable conditions. Returns (should_disable, reason)."""
        trades = list(state.trades)

        # Rule 1: consecutive loss pause
        if state.consecutive_losses >= MAX_CONSECUTIVE_LOSSES:
            return True, f"CONSECUTIVE_LOSSES_{state.consecutive_losses}"

        # Rule 2: win rate crash (only when we have enough data)
        wr_trades = trades[-WIN_RATE_LOOKBACK:]
        if len(wr_trades) >= WIN_RATE_LOOKBACK:
            winners  = [t for t in wr_trades if t > 0]
            win_rate = len(winners) / len(wr_trades) * 100
            if win_rate < WIN_RATE_MIN_PCT:
                return True, f"WIN_RATE_TOO_LOW ({win_rate:.1f}% < {WIN_RATE_MIN_PCT}%)"

        # Rule 3: profit factor collapse
        pf_trades = trades[-PF_LOOKBACK:]
        if len(pf_trades) >= PF_LOOKBACK:
            gross_profit = sum(t for t in pf_trades if t > 0)
            gross_loss   = abs(sum(t for t in pf_trades if t < 0))
            if gross_loss > 0:
                pf = gross_profit / gross_loss
                if pf < PF_MIN:
                    return True, f"PROFIT_FACTOR_TOO_LOW ({pf:.2f} < {PF_MIN})"

        return False, ""


# ── Singleton instance used by the pipeline ──
strategy_health_monitor = StrategyHealthMonitor()
