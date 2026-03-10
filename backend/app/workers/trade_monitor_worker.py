"""
Trade Monitor Worker — Standalone service (Phase 7).

Monitors open paper trades and live trades for SL/target hits, trailing stops,
and EOD square-off. Runs on a polling loop every 10-15 seconds.

Run:
    python -m app.workers.trade_monitor_worker
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta

from sqlalchemy import select, and_, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_session_factory
from app.core.logging import logger
from app.models.paper_trade import PaperTrade
from app.workers.base import WorkerBase


IST = timezone(timedelta(hours=5, minutes=30))

# ATR trailing: after LTP moves 1× initial risk past entry, trail by 1× risk
TRAIL_TRIGGER_RATIO = 1.0     # 1 risk-unit profit triggers trailing
TRAIL_DISTANCE_RATIO = 1.0    # trail SL stays 1 risk-unit behind current price

POLL_INTERVAL_SECONDS = 15    # How often to check trades


def _is_eod() -> bool:
    """Check if current IST time is past 15:15 (EOD square-off time)."""
    now_ist = datetime.now(IST)
    return now_ist.hour >= 15 and now_ist.minute >= 15


class TradeMonitorWorker(WorkerBase):
    """
    Periodically checks all OPEN trades for exit conditions:
      1. SL hit
      2. Target hit (T1 partial, T2 full)
      3. Trailing stop updates
      4. EOD square-off at 15:15 IST
    """

    NAME = "trade-monitor-worker"

    # ── Price Fetching ───────────────────────────────────────────────────────

    async def _get_ltp(self, symbol: str, instrument_key: str = "") -> float | None:
        """
        Fetch Last Traded Price.
        Priority: WebSocket cache → Upstox HTTP → Angel HTTP → Yahoo Finance.
        """
        # 1. Try WebSocket cache (fastest, no API call)
        try:
            from app.ingestion.websocket_manager import market_data_cache
            cached = market_data_cache.get_ltp_if_fresh(instrument_key or symbol)
            if cached is not None:
                return cached
        except Exception:
            pass

        # 2. Try Upstox HTTP
        try:
            from app.ingestion.upstox_http import UpstoxHTTPClient
            client = UpstoxHTTPClient()
            if instrument_key:
                ltp = await client.get_ltp(instrument_key)
                if ltp and ltp > 0:
                    return ltp
        except Exception:
            pass

        # 3. Try Angel HTTP
        try:
            from app.ingestion.angel_http import AngelOneHTTPClient
            angel = AngelOneHTTPClient()
            ltp = await angel.get_ltp(symbol)
            if ltp and ltp > 0:
                return ltp
        except Exception:
            pass

        # 4. Fallback: Yahoo Finance
        try:
            import yfinance as yf
            ticker = yf.Ticker(f"{symbol}.NS")
            data = ticker.history(period="1d")
            if not data.empty:
                return float(data["Close"].iloc[-1])
        except Exception:
            pass

        return None

    # ── Paper Trade Monitoring ───────────────────────────────────────────────

    async def _check_paper_trades(self):
        """Check all OPEN paper trades against current LTP."""
        try:
            async with async_session_factory() as db:
                result = await db.execute(
                    select(PaperTrade).where(PaperTrade.status == "OPEN")
                )
                open_trades = result.scalars().all()

                if not open_trades:
                    return

                eod = _is_eod()

                from app.core.redis import redis_client
                from app.system.trading_state import get_trading_state
                trading_state = await get_trading_state(redis_client)
                emergency_flatten = trading_state == "EMERGENCY_FLATTEN"

                for trade in open_trades:
                    try:
                        # Get instrument_key if available
                        instrument_key = getattr(trade, "instrument_key", "") or ""
                        ltp = await self._get_ltp(trade.symbol, instrument_key)

                        if ltp is None:
                            continue

                        # EOD Square-off or Emergency Flatten
                        if eod or emergency_flatten:
                            today_str = datetime.now(IST).strftime("%Y-%m-%d")
                            lock_reason = "eod" if eod else "emergency"
                            lock_key = f"{lock_reason}_square_off_lock:paper:{trade.id}:{today_str}"
                            if await redis_client.set(lock_key, "1", ex=3600, nx=True):
                                trade.exit_price = ltp
                                trade.exit_time = datetime.now(timezone.utc)
                                trade.status = "CLOSED"
                                trade.exit_reason = "EMERGENCY_FLATTEN" if emergency_flatten else "EOD_SQUAREOFF"
                                pnl = self._calc_pnl(trade, ltp)
                                trade.realised_pnl = pnl
                                await self._add_to_daily_pnl(db, pnl)
                                logger.info(
                                    f"[{self.NAME}] Paper {lock_reason} square-off: {trade.symbol} @ ₹{ltp:.2f} "
                                    f"P&L=₹{pnl:.2f}"
                                )
                            continue

                        # Check SL hit
                        sl_hit = False
                        if trade.direction == "BUY" and ltp <= trade.stop_loss:
                            sl_hit = True
                        elif trade.direction == "SELL" and ltp >= trade.stop_loss:
                            sl_hit = True

                        if sl_hit:
                            trade.exit_price = trade.stop_loss
                            trade.exit_time = datetime.now(timezone.utc)
                            trade.status = "CLOSED"
                            trade.exit_reason = "SL_HIT"
                            pnl = self._calc_pnl(trade, trade.stop_loss)
                            trade.realised_pnl = pnl
                            await self._add_to_daily_pnl(db, pnl)
                            logger.info(
                                f"[{self.NAME}] SL hit: {trade.symbol} @ ₹{trade.stop_loss:.2f} "
                                f"P&L=₹{pnl:.2f}"
                            )
                            continue

                        # Check Target hit
                        target_hit = False
                        if trade.direction == "BUY" and ltp >= trade.target_price:
                            target_hit = True
                        elif trade.direction == "SELL" and ltp <= trade.target_price:
                            target_hit = True

                        if target_hit:
                            trade.exit_price = trade.target_price
                            trade.exit_time = datetime.now(timezone.utc)
                            trade.status = "CLOSED"
                            trade.exit_reason = "TARGET_HIT"
                            pnl = self._calc_pnl(trade, trade.target_price)
                            trade.realised_pnl = pnl
                            await self._add_to_daily_pnl(db, pnl)
                            logger.info(
                                f"[{self.NAME}] Target hit: {trade.symbol} "
                                f"@ ₹{trade.target_price:.2f} P&L=₹{pnl:.2f}"
                            )
                            continue

                        # Trailing stop logic
                        await self._update_trailing_stop(trade, ltp)

                    except Exception as e:
                        logger.exception(
                            f"[{self.NAME}] Error checking trade {trade.symbol}: {e}"
                        )

                await db.commit()

        except Exception as e:
            logger.exception(f"[{self.NAME}] check_paper_trades error: {e}")

    # ── Live Trade Monitoring ────────────────────────────────────────────────

    async def _check_live_trades(self):
        """Check all OPEN live trades (similar logic to paper trades)."""
        try:
            from app.models.live_trade import LiveTrade
            async with async_session_factory() as db:
                result = await db.execute(
                    select(LiveTrade).where(LiveTrade.status == "OPEN")
                )
                open_trades = result.scalars().all()

                if not open_trades:
                    return

                # ── Global Kill Switch Check (Emergency Flatten) ──
                from app.core.redis import redis_client
                from app.system.trading_state import get_trading_state
                trading_state = await get_trading_state(redis_client)
                emergency_flatten = trading_state == "EMERGENCY_FLATTEN"

                eod = _is_eod()

                for trade in open_trades:
                    try:
                        instrument_key = getattr(trade, "instrument_key", "") or ""
                        ltp = await self._get_ltp(trade.symbol, instrument_key)

                        if ltp is None:
                            continue

                        # EOD Square-off or Emergency Flatten for live trades
                        if eod or emergency_flatten:
                            today_str = datetime.now(IST).strftime("%Y-%m-%d")
                            lock_reason = "eod" if eod else "emergency"
                            lock_key = f"{lock_reason}_square_off_lock:live:{trade.id}:{today_str}"
                            if await redis_client.set(lock_key, "1", ex=3600, nx=True):
                                try:
                                    from app.engine.execution_manager import ExecutionManager
                                    mgr = ExecutionManager(db)
                                    # Use place_market_close_order instead of non-existent square_off_trade
                                    success = await mgr.place_market_close_order(trade)
                                    if success:
                                        logger.info(f"[{self.NAME}] Live {lock_reason} square-off submitted: {trade.symbol}")
                                    else:
                                        logger.error(f"[{self.NAME}] Live {lock_reason} square-off submission FAILED for {trade.symbol}")
                                        await redis_client.delete(lock_key)
                                except Exception as e:
                                    logger.exception(
                                        f"[{self.NAME}] Live {lock_reason} square-off exception for {trade.symbol}: {e}"
                                    )
                                    await redis_client.delete(lock_key)
                            continue

                        # Fix Group 1: Fallback stop-loss monitor
                        if not trade.sl_order_id:
                            # ADDITIONAL SAFETY IMPROVEMENT: Recreate missing SL
                            logger.warning(f"[{self.NAME}] Missing SL for {trade.symbol}. Recreating protection order.")
                            try:
                                from app.engine.execution_manager import ExecutionManager
                                mgr = ExecutionManager(db)
                                new_sl = await mgr.place_sl_order(trade, trade.stop_loss)
                                if new_sl:
                                    trade.sl_order_id = new_sl
                                    logger.info(f"[{self.NAME}] Successfully recreated SL for {trade.symbol}: {new_sl}")
                            except Exception as e:
                                logger.error(f"[{self.NAME}] Failed to recreate SL for {trade.symbol}: {e}")

                            local_sl_hit = False
                            if trade.direction == "BUY" and ltp <= trade.stop_loss:
                                local_sl_hit = True
                            elif trade.direction == "SELL" and ltp >= trade.stop_loss:
                                local_sl_hit = True

                            if local_sl_hit:
                                logger.error(f"[{self.NAME}] CRITICAL: Local SL triggered for naked trade {trade.symbol}")
                                try:
                                    from app.engine.execution_manager import ExecutionManager
                                    mgr = ExecutionManager(db)
                                    success = await mgr.place_market_close_order(trade)
                                    if success:
                                        logger.info(f"[{self.NAME}] Local SL fallback close submitted: {trade.symbol}")
                                    else:
                                        logger.error(f"[{self.NAME}] Local SL fallback FAILED for {trade.symbol}")
                                except Exception as e:
                                    logger.exception(f"[{self.NAME}] Local SL fallback exception for {trade.symbol}: {e}")
                                continue

                        # Fix Group 8: Apply real broker trailing stops
                        await self._update_trailing_stop(trade, ltp, db)

                    except Exception as e:
                        logger.exception(
                            f"[{self.NAME}] Error checking live trade {trade.symbol}: {e}"
                        )

                await db.commit()

        except Exception as e:
            logger.exception(f"[{self.NAME}] check_live_trades error: {e}")

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _calc_pnl(self, trade, exit_price: float) -> float:
        """Calculate realised Net P&L for a trade including estimated fees."""
        qty = float(getattr(trade, "quantity", 1) or 1)
        if trade.direction == "BUY":
            gross_pnl = round(float(exit_price - float(trade.entry_price)) * qty, 2)
        else:
            gross_pnl = round(float(float(trade.entry_price) - exit_price) * qty, 2)
            
        # Fix Group 7: Net PnL circuit breakers (deduct costs per trade)
        cost = 40.0 + (float(trade.entry_price) * qty * 0.0005)
        return round(gross_pnl - cost, 2)

    async def _add_to_daily_pnl(self, db, pnl: float) -> None:
        """Add realised PnL to today's risk state."""
        from datetime import date
        from app.models.daily_risk_state import DailyRiskState
        today = date.today()
        result = await db.execute(select(DailyRiskState).where(DailyRiskState.trade_date == today))
        state = result.scalar_one_or_none()
        if state:
            current_pnl = float(state.realised_pnl or 0.0)
            state.realised_pnl = round(current_pnl + pnl, 2)

    async def _update_trailing_stop(self, trade, ltp: float, db=None) -> None:
        """Update trailing stop locally and via broker if price has moved favorably."""
        from sqlalchemy.ext.asyncio import AsyncSession
        entry = trade.entry_price
        sl = trade.stop_loss
        initial_risk = abs(entry - sl)

        if initial_risk <= 0:
            return

        new_sl = None
        if trade.direction == "BUY":
            profit_distance = ltp - entry
            trigger = initial_risk * TRAIL_TRIGGER_RATIO

            if profit_distance >= trigger:
                proposed_sl = ltp - (initial_risk * TRAIL_DISTANCE_RATIO)
                if proposed_sl > trade.stop_loss:
                    new_sl = proposed_sl
        else:  # SELL
            profit_distance = entry - ltp
            trigger = initial_risk * TRAIL_TRIGGER_RATIO

            if profit_distance >= trigger:
                proposed_sl = ltp + (initial_risk * TRAIL_DISTANCE_RATIO)
                if proposed_sl < trade.stop_loss:
                    new_sl = proposed_sl

        if new_sl is not None:
            old_sl = trade.stop_loss
            trade.stop_loss = round(float(new_sl), 2)
            logger.debug(
                f"[{self.NAME}] Trail SL: {trade.symbol} "
                f"₹{old_sl:.2f} → ₹{trade.stop_loss:.2f}"
            )
            # Fix Group 8: Broker trailing stop integration (Atomic Modification)
            if db and hasattr(trade, "sl_order_id") and trade.sl_order_id:
                try:
                    from app.engine.execution_manager import ExecutionManager
                    import asyncio
                    mgr = ExecutionManager(db)
                    success = False
                    for attempt in range(1, 4):
                        success = await mgr.modify_order(trade.sl_order_id, trade.stop_loss)
                        if success:
                            break
                        await asyncio.sleep(1)
                    
                    if not success:
                        logger.error(f"[{self.NAME}] Failed to modify broker trailing SL for {trade.symbol} after 3 attempts")
                except Exception as e:
                    logger.error(f"[{self.NAME}] Exception modifying broker trailing SL for {trade.symbol}: {e}")

    # ── Main Loop ────────────────────────────────────────────────────────────

    async def run(self):
        """Main loop — poll for open trades every POLL_INTERVAL_SECONDS."""
        logger.info(f"[{self.NAME}] Entering monitoring loop (interval={POLL_INTERVAL_SECONDS}s)")

        while self.is_running:
            try:
                await self._check_paper_trades()
                await self._check_live_trades()
            except Exception as e:
                logger.exception(f"[{self.NAME}] Monitor cycle error: {e}")

            # Sleep in small increments so shutdown is responsive
            for _ in range(POLL_INTERVAL_SECONDS):
                if not self.is_running:
                    break
                await asyncio.sleep(1)


# ── Entry Point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    TradeMonitorWorker().main()
