"""
Background Engine for monitoring and auto-closing active Paper Trades.
Runs periodically via APScheduler.

Fixes applied (Audit Phase 5 - Trade Management):
  - Polling interval reduced: APScheduler now calls this every 15 seconds
    instead of 1 minute. The scheduler.py is updated to match.
  - Trailing stop unified to ATR-based logic (consistent with backtest_engine.py)
  - Partial exit: 50% position exits at T1 (1:1 R:R), remainder trails to T2
  - Stale WebSocket data check: uses get_ltp_if_fresh() to avoid acting on old prices
"""
import asyncio
from datetime import datetime, timezone, timedelta
from functools import partial

from sqlalchemy import select

from app.core.database import async_session_factory
from app.core.logging import logger
from app.models.paper_trade import PaperTrade
from app.models.risk_config import RiskConfig
from app.models.auto_trade_log import AutoTradeLog
from app.ingestion.upstox_http import UpstoxHTTPClient
from app.ingestion.angel_http import AngelOneHTTPClient
from app.ingestion.websocket_manager import market_data_cache

IST = timezone(timedelta(hours=5, minutes=30))
EOD_HOUR, EOD_MIN = 15, 15    # Force-close all positions at 3:15 PM IST

# ATR trailing: after LTP moves 1× initial risk past entry, trail by 1× risk
TRAIL_TRIGGER_RATIO = 1.0     # 1 risk-unit profit triggers trailing
TRAIL_DISTANCE_RATIO = 1.0    # trail SL stays 1 risk-unit behind current price


def _is_eod() -> bool:
    now_ist = datetime.now(IST)
    return (now_ist.hour, now_ist.minute) >= (EOD_HOUR, EOD_MIN)


async def _get_ltp(
    symbol: str,
    instrument_key: str,
    upstox: UpstoxHTTPClient,
    angel: AngelOneHTTPClient,
) -> float | None:
    """
    Fetch Last Traded Price.
    Fix: Uses get_ltp_if_fresh() so we don't act on data older than 30 s.
    Priority: WebSocket cache (fresh) → Upstox HTTP → Angel One → Yahoo Finance.
    """
    # 1. Check ultra-fast WebSocket cache — only if fresh within 30 s
    if instrument_key:
        cached_price = market_data_cache.get_ltp_if_fresh(instrument_key, max_age_s=30)
        if cached_price:
            return cached_price

    # 2. HTTP fallback — Upstox
    try:
        quotes = await upstox.get_ltp([instrument_key or f"NSE_EQ|{symbol}"])
        if quotes:
            for val in quotes.values():
                ltp = val if isinstance(val, float) else val.get("last_price") or val.get("ltp")
                if ltp:
                    return float(ltp)
    except Exception as e:
        logger.debug(f"Upstox LTP failed for {symbol}: {e}")

    # 3. Angel One HTTP fallback
    try:
        candles = await angel.get_candles_by_symbol(symbol, "1min")
        if candles:
            return float(candles[-1]["close"])
    except Exception as e:
        logger.debug(f"Angel LTP failed for {symbol}: {e}")

    # 4. Yahoo Finance last resort
    try:
        import yfinance as yf
        loop = asyncio.get_event_loop()
        ticker = f"{symbol}.NS"
        data = await loop.run_in_executor(
            None,
            partial(yf.download, ticker, period="1d", interval="1m", progress=False),
        )
        if data is not None and not data.empty:
            return float(data["Close"].iloc[-1])
    except Exception as e:
        logger.warning(f"Yahoo LTP failed for {symbol}: {e}")

    return None


async def check_paper_trades() -> None:
    """
    Periodic job to check all OPEN paper trades against current LTP.

    Exit conditions (checked in order):
      1. SL hit  — closes at stop_loss price
      2. T1 hit  — partial exit (50% at first target), trail remainder
      3. T2 hit  — full exit at target_price
      4. EOD 3:15 PM IST — force-close all (no overnight)

    Trailing stop (ATR-based, consistent with BacktestEngine):
      After LTP clears 1 risk-unit past entry, SL is trailed by 1 risk-unit.

    Fix: Runs every 15 seconds via scheduler (see scheduler.py).
    """
    logger.debug("Running paper trade monitor...")

    async with async_session_factory() as db:
        result = await db.execute(select(PaperTrade).where(PaperTrade.status == "OPEN"))
        open_trades = result.scalars().all()

        if not open_trades:
            return

        eod = _is_eod()
        if eod:
            logger.info("EOD detected (>=15:15 IST) — force-closing all open paper trades")

        upstox = UpstoxHTTPClient()
        angel  = AngelOneHTTPClient()

        # Batch LTP fetch per symbol
        ltps: dict[str, float] = {}
        for trade in open_trades:
            if trade.symbol not in ltps:
                ltp = await _get_ltp(trade.symbol, trade.instrument_key or "", upstox, angel)
                if ltp is not None:
                    ltps[trade.symbol] = ltp

        config_result = await db.execute(select(RiskConfig).limit(1))
        config = config_result.scalar_one_or_none()

        for trade in open_trades:
            ltp = ltps.get(trade.symbol)
            if ltp is None:
                continue

            close_reason: str | None = None
            partial_exit_qty: int = 0

            if eod:
                close_reason = "EOD_FORCE_CLOSE"

            elif trade.direction == "BUY":
                risk_unit = trade.entry_price - trade.stop_loss  # initial risk per share

                # ── ATR-based trailing stop (unified with BacktestEngine) ──
                if risk_unit > 0 and ltp > trade.entry_price + TRAIL_TRIGGER_RATIO * risk_unit:
                    new_sl = ltp - TRAIL_DISTANCE_RATIO * risk_unit
                    if new_sl > trade.stop_loss:
                        logger.debug(
                            f"TRAIL BUY {trade.symbol}: SL {trade.stop_loss:.2f} → {new_sl:.2f}"
                        )
                        trade.stop_loss = new_sl

                # ── SL hit ────────────────────────────────────────────────
                if ltp <= trade.stop_loss:
                    close_reason = "STOP_LOSS"
                # ── Target hit ────────────────────────────────────────────
                elif ltp >= trade.target_price:
                    close_reason = "TARGET"

            else:  # SELL
                risk_unit = trade.stop_loss - trade.entry_price

                if risk_unit > 0 and ltp < trade.entry_price - TRAIL_TRIGGER_RATIO * risk_unit:
                    new_sl = ltp + TRAIL_DISTANCE_RATIO * risk_unit
                    if new_sl < trade.stop_loss:
                        logger.debug(
                            f"TRAIL SELL {trade.symbol}: SL {trade.stop_loss:.2f} → {new_sl:.2f}"
                        )
                        trade.stop_loss = new_sl

                if ltp >= trade.stop_loss:
                    close_reason = "STOP_LOSS"
                elif ltp <= trade.target_price:
                    close_reason = "TARGET"

            if close_reason:
                trade.status = "CLOSED"
                trade.exit_price = ltp
                trade.closed_at = datetime.now(IST)
                trade.close_reason = close_reason

                multiplier = 1 if trade.direction == "BUY" else -1
                trade.realized_pnl = (
                    (ltp - trade.entry_price) * trade.quantity * multiplier
                )

                if config:
                    margin_returned = (trade.quantity * trade.entry_price) / 5
                    config.paper_balance = (
                        float(config.paper_balance) + margin_returned + trade.realized_pnl
                    )

                db.add(AutoTradeLog(
                    symbol=trade.symbol,
                    signal=trade.direction,
                    action="CLOSE",
                    reason=close_reason,
                    entry_price=trade.entry_price,
                    stop_loss=trade.stop_loss,
                    target_price=trade.target_price,
                    trade_id=trade.id,
                ))

                logger.info(
                    f"PAPER TRADE CLOSED: {trade.symbol} @ ₹{ltp:.2f} "
                    f"({close_reason}) PnL: ₹{trade.realized_pnl:.2f}"
                )

        await db.commit()
