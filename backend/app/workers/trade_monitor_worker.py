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

from sqlalchemy import select, and_
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

                for trade in open_trades:
                    try:
                        # Get instrument_key if available
                        instrument_key = getattr(trade, "instrument_key", "") or ""
                        ltp = await self._get_ltp(trade.symbol, instrument_key)

                        if ltp is None:
                            continue

                        # EOD Square-off
                        if eod:
                            trade.exit_price = ltp
                            trade.exit_time = datetime.now(timezone.utc)
                            trade.status = "CLOSED"
                            trade.exit_reason = "EOD_SQUAREOFF"
                            pnl = self._calc_pnl(trade, ltp)
                            trade.realised_pnl = pnl
                            logger.info(
                                f"[{self.NAME}] EOD square-off: {trade.symbol} @ ₹{ltp:.2f} "
                                f"P&L=₹{pnl:.2f}"
                            )
                            continue

                        # Check SL hit
                        sl_hit = False
                        if trade.signal == "BUY" and ltp <= trade.stop_loss:
                            sl_hit = True
                        elif trade.signal == "SELL" and ltp >= trade.stop_loss:
                            sl_hit = True

                        if sl_hit:
                            trade.exit_price = trade.stop_loss
                            trade.exit_time = datetime.now(timezone.utc)
                            trade.status = "CLOSED"
                            trade.exit_reason = "SL_HIT"
                            pnl = self._calc_pnl(trade, trade.stop_loss)
                            trade.realised_pnl = pnl
                            logger.info(
                                f"[{self.NAME}] SL hit: {trade.symbol} @ ₹{trade.stop_loss:.2f} "
                                f"P&L=₹{pnl:.2f}"
                            )
                            continue

                        # Check Target hit
                        target_hit = False
                        if trade.signal == "BUY" and ltp >= trade.target_price:
                            target_hit = True
                        elif trade.signal == "SELL" and ltp <= trade.target_price:
                            target_hit = True

                        if target_hit:
                            trade.exit_price = trade.target_price
                            trade.exit_time = datetime.now(timezone.utc)
                            trade.status = "CLOSED"
                            trade.exit_reason = "TARGET_HIT"
                            pnl = self._calc_pnl(trade, trade.target_price)
                            trade.realised_pnl = pnl
                            logger.info(
                                f"[{self.NAME}] Target hit: {trade.symbol} "
                                f"@ ₹{trade.target_price:.2f} P&L=₹{pnl:.2f}"
                            )
                            continue

                        # Trailing stop logic
                        self._update_trailing_stop(trade, ltp)

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

                eod = _is_eod()

                for trade in open_trades:
                    try:
                        instrument_key = getattr(trade, "instrument_key", "") or ""
                        ltp = await self._get_ltp(trade.symbol, instrument_key)

                        if ltp is None:
                            continue

                        # EOD Square-off for live trades
                        if eod:
                            try:
                                from app.engine.execution_manager import ExecutionManager
                                mgr = ExecutionManager(db)
                                await mgr.square_off_trade(trade, reason="EOD_SQUAREOFF")
                                logger.info(
                                    f"[{self.NAME}] Live EOD square-off: {trade.symbol}"
                                )
                            except Exception as e:
                                logger.exception(
                                    f"[{self.NAME}] Live EOD square-off failed {trade.symbol}: {e}"
                                )

                    except Exception as e:
                        logger.exception(
                            f"[{self.NAME}] Error checking live trade {trade.symbol}: {e}"
                        )

                await db.commit()

        except Exception as e:
            logger.exception(f"[{self.NAME}] check_live_trades error: {e}")

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _calc_pnl(self, trade, exit_price: float) -> float:
        """Calculate realised P&L for a trade."""
        qty = getattr(trade, "quantity", 1) or 1
        if trade.signal == "BUY":
            return round((exit_price - trade.entry_price) * qty, 2)
        else:
            return round((trade.entry_price - exit_price) * qty, 2)

    def _update_trailing_stop(self, trade, ltp: float):
        """Update trailing stop if price has moved favorably."""
        entry = trade.entry_price
        sl = trade.stop_loss
        initial_risk = abs(entry - sl)

        if initial_risk <= 0:
            return

        if trade.signal == "BUY":
            profit_distance = ltp - entry
            trigger = initial_risk * TRAIL_TRIGGER_RATIO

            if profit_distance >= trigger:
                new_sl = ltp - (initial_risk * TRAIL_DISTANCE_RATIO)
                if new_sl > trade.stop_loss:
                    old_sl = trade.stop_loss
                    trade.stop_loss = round(new_sl, 2)
                    logger.debug(
                        f"[{self.NAME}] Trail SL: {trade.symbol} "
                        f"₹{old_sl:.2f} → ₹{trade.stop_loss:.2f}"
                    )
        else:  # SELL
            profit_distance = entry - ltp
            trigger = initial_risk * TRAIL_TRIGGER_RATIO

            if profit_distance >= trigger:
                new_sl = ltp + (initial_risk * TRAIL_DISTANCE_RATIO)
                if new_sl < trade.stop_loss:
                    old_sl = trade.stop_loss
                    trade.stop_loss = round(new_sl, 2)
                    logger.debug(
                        f"[{self.NAME}] Trail SL: {trade.symbol} "
                        f"₹{old_sl:.2f} → ₹{trade.stop_loss:.2f}"
                    )

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
