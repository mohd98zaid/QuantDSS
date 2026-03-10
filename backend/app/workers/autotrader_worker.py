"""
AutoTrader Worker — Standalone service (Phase 6).

Consumes risk-passed signals from `signals:risk_passed`, executes trades
(paper or live mode), and publishes to `signals:executed`.

Run:
    python -m app.workers.autotrader_worker
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone, timedelta

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from app.core.database import async_session_factory
from app.core.logging import logger
from app.core.streams import (
    STREAM_SIGNALS_RISK_PASSED,
    STREAM_SIGNALS_EXECUTED,
    consume_stream,
    publish_to_stream,
)
from app.system.trading_state import is_trading_enabled
from app.models.auto_trade_config import AutoTradeConfig
from app.models.paper_trade import PaperTrade
from app.workers.base import WorkerBase
from app.alerts.telegram_notifier import send_telegram_alert


IST = timezone(timedelta(hours=5, minutes=30))


def _is_market_hours() -> bool:
    """Check if current IST time is within market hours (9:15-15:15)."""
    now_ist = datetime.now(IST)
    market_open = now_ist.replace(hour=9, minute=15, second=0, microsecond=0)
    market_close = now_ist.replace(hour=15, minute=15, second=0, microsecond=0)
    return market_open <= now_ist <= market_close


class AutoTraderWorker(WorkerBase):
    """
    Consumes risk-approved signals and executes trades.
    Supports paper trading and live trading modes.
    """

    NAME = "autotrader-worker"
    CONSUMER_GROUP = "autotrader_group"
    CONSUMER_NAME = "autotrader_1"

    def __init__(self):
        super().__init__()

    # ── Signal Handler ───────────────────────────────────────────────────────

    async def _handle_signal(self, msg_id: str, data: dict[str, str]):
        """Execute a trade for a risk-approved signal."""
        symbol_name = data.get("symbol_name", "")
        trace_id = data.get("_trace_id") or msg_id
        strategy_name = data.get("strategy", "")
        local_logger = logger.bind(symbol=symbol_name, signal_id=trace_id, strategy=strategy_name)
        
        local_logger.info(f"[{self.NAME}] Incoming signal from risk_passed: {data}")
        signal_type = data.get("signal_type", "")
        entry_price = float(data.get("entry_price", "0"))
        stop_loss = float(data.get("stop_loss", "0"))
        target_price = float(data.get("target_price", "0"))
        quantity = int(float(data.get("quantity", "0")))
        risk_amount = float(data.get("risk_amount", "0"))
        candle_time_str = data.get("candle_time", "")
        contributing_strategies = data.get("contributing_strategies", "[]")
        quality_score = data.get("quality_score", "0")
        symbol_id = int(data.get("symbol_id", "0"))

        if not symbol_name or not signal_type:
            local_logger.warning(f"[{self.NAME}] Invalid signal data: {data}")
            return
            
        import time
        start_time = time.time()

        is_replay = data.get("is_replay", "").lower() in ("true", "1", "yes")

        # Fix Group 5: Stale Signal Execution Prevention
        is_scanner = data.get("is_scanner", "").lower() in ("true", "1", "yes")
        if not is_replay and not is_scanner and candle_time_str:
            try:
                candle_time = datetime.fromisoformat(candle_time_str)
                age = (datetime.now(timezone.utc) - candle_time).total_seconds()
                if age > 60:
                    local_logger.warning(f"[{self.NAME}] Stale signal rejected (age: {age:.1f}s > 60s): {symbol_name} {signal_type}")
                    return
            except ValueError:
                local_logger.warning(f"[{self.NAME}] Invalid candle_time format: {candle_time_str}")

        # Idempotency check: hash unique traits deterministically (Fix Group 2)
        signal_hash = f"{symbol_name}_{signal_type}_{contributing_strategies}_{candle_time_str}"
        idempotency_key = f"executed_signal:{signal_hash}"
        
        from app.core.redis import redis_client
        is_new = await redis_client.set(idempotency_key, "1", ex=900, nx=True)
        if not is_new:
            local_logger.info(f"[{self.NAME}] Idempotency skip (already executed): {signal_hash}")
            return

        # Fix Group 5: Extract reservation values to release them after execution attempt
        risk_amount = float(data.get("risk_amount", 0))
        quantity = int(float(data.get("quantity", 0)))
        entry_price = float(data.get("entry_price", 0))
        notional = quantity * entry_price

        try:
            # ── Global Kill Switch Check ──
            from app.core.redis import redis_client
            if not await is_trading_enabled(redis_client):
                local_logger.warning(f"[{self.NAME}] AutoTrader halted by global kill switch — skipping {symbol_name}")
                return

            # Check market hours — bypass for replay sessions so historical data can be tested
            if not is_replay and not _is_market_hours():
                local_logger.info(f"[{self.NAME}] Outside market hours — skipping {symbol_name} {signal_type}")
                return
            if is_replay:
                local_logger.info(f"[{self.NAME}] 🔄 Replay signal — bypassing market hours check for {symbol_name} {signal_type}")

            async with async_session_factory() as db:
                # Load auto-trade configuration
                result = await db.execute(select(AutoTradeConfig).limit(1))
                cfg = result.scalar_one_or_none()

                if cfg is None or not cfg.enabled:
                    logger.info(f"[{self.NAME}] AutoTrader disabled — skipping {symbol_name}")
                    return

                mode = getattr(cfg, "mode", "paper")
                
                # Fix 3: Entry Price Validation
                if entry_price <= 0:
                    local_logger.warning(f"Invalid entry price {entry_price} for signal {trace_id}")
                    return
                
                # Fix 2: Max Open Positions Validation -> fallback worker check
                from app.models.live_trade import LiveTrade
                from app.models.paper_trade import PaperTrade
                from sqlalchemy import func
                
                paper_count = (await db.execute(select(func.count()).select_from(PaperTrade).where(PaperTrade.status == "OPEN"))).scalar_one() or 0
                live_count = (await db.execute(select(func.count()).select_from(LiveTrade).where(LiveTrade.status == "OPEN"))).scalar_one() or 0
                active_positions = paper_count + live_count
                
                if active_positions >= cfg.max_open_positions:
                    local_logger.warning("Trade rejected: max open positions reached")
                    return

                if mode == "paper":
                    await self._execute_paper_trade(
                        db=db,
                        cfg=cfg,
                        symbol_name=symbol_name,
                        signal_type=signal_type,
                        entry_price=entry_price,
                        stop_loss=stop_loss,
                        target_price=target_price,
                        quantity=quantity,
                        risk_amount=risk_amount,
                        strategy=contributing_strategies,
                        quality_score=quality_score,
                        symbol_id=symbol_id,
                    )
                else:
                    await self._execute_live_trade(
                        db=db,
                        cfg=cfg,
                        symbol_name=symbol_name,
                        signal_type=signal_type,
                        entry_price=entry_price,
                        stop_loss=stop_loss,
                        target_price=target_price,
                        quantity=quantity,
                        risk_amount=risk_amount,
                        strategy=contributing_strategies,
                        symbol_id=symbol_id,
                    )

            # Publish executed signal
            executed_message = {
                "trade_id": "",  # Filled after DB insert
                "symbol": symbol_name,
                "signal_type": signal_type,
                "entry_price": str(entry_price),
                "quantity": str(quantity),
                "mode": mode,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            await publish_to_stream(STREAM_SIGNALS_EXECUTED, executed_message)
            
        finally:
            # Fix Group 5: Release risk reservations
            try:
                from app.core.redis import redis_client
                trace_id = data.get("_trace_id")
                if trace_id:
                    await redis_client.delete(f"risk_reservation:{trace_id}")
            except Exception as e:
                logger.error(f"[{self.NAME}] Failed to release risk reservations: {e}")

    # ── Paper Trade Execution ────────────────────────────────────────────────

    async def _execute_paper_trade(
        self,
        db: AsyncSession,
        cfg: AutoTradeConfig,
        symbol_name: str,
        signal_type: str,
        entry_price: float,
        stop_loss: float,
        target_price: float,
        quantity: int,
        risk_amount: float,
        strategy: str,
        quality_score: str,
        symbol_id: int,
    ):
        """Execute a paper trade — write to paper_trades table and deduct margin."""
        from app.models.risk_config import RiskConfig
        
        # Check for duplicate open trades
        existing = await db.execute(
            select(PaperTrade).where(
                and_(
                    PaperTrade.symbol == symbol_name,
                    PaperTrade.status == "OPEN",
                    PaperTrade.direction == signal_type,
                )
            ).limit(1)
        )
        if existing.scalar_one_or_none():
            logger.info(f"[{self.NAME}] Duplicate paper trade skipped: {symbol_name} {signal_type}")
            return

        # Compute quantity from config if not provided by risk engine
        if quantity <= 0:
            capital = getattr(cfg, "capital_per_trade", 10000.0) or 10000.0
            quantity = max(1, int(capital / entry_price))

        # Lock margin from virtual balance
        margin_req = (quantity * entry_price) / 5.0
        risk_cfg = (await db.execute(select(RiskConfig).with_for_update().limit(1))).scalar_one_or_none()
        
        if risk_cfg:
            if float(risk_cfg.paper_balance) < margin_req:
                logger.warning(
                    f"[{self.NAME}] 🛑 Paper trade rejected: Insufficient virtual balance. "
                    f"Required: ₹{margin_req:.2f}, Available: ₹{float(risk_cfg.paper_balance):.2f}"
                )
                return
            risk_cfg.paper_balance = float(risk_cfg.paper_balance) - margin_req
            db.add(risk_cfg)

        trade = PaperTrade(
            symbol=symbol_name,
            direction=signal_type,
            entry_price=entry_price,
            stop_loss=stop_loss,
            target_price=target_price,
            quantity=quantity,
            status="OPEN",
            # removed strategy and entry_time as they don't exist in model
        )

        db.add(trade)
        await db.commit()

        logger.info(
            f"[{self.NAME}] 📝 Paper trade opened: {signal_type} {quantity}x {symbol_name} "
            f"@ ₹{entry_price:.2f} (SL ₹{stop_loss:.2f}, T ₹{target_price:.2f})"
        )
        
        from app.core.metrics import TRADES_EXECUTED
        TRADES_EXECUTED.labels(symbol=symbol_name, strategy=strategy, type="paper").inc()
        
        send_telegram_alert(f"Paper trade executed: {symbol_name} @ {entry_price:.2f}")

    # ── Live Trade Execution ─────────────────────────────────────────────────

    async def _execute_live_trade(
        self,
        db: AsyncSession,
        cfg: AutoTradeConfig,
        symbol_name: str,
        signal_type: str,
        entry_price: float,
        stop_loss: float,
        target_price: float,
        quantity: int,
        risk_amount: float,
        strategy: str,
        symbol_id: int,
    ):
        """Execute a live trade via broker ExecutionManager."""
        try:
            from app.engine.execution_manager import ExecutionManager
            mgr = ExecutionManager(db)

            # Determine instrument key from symbol
            from app.models.symbol import Symbol
            result = await db.execute(
                select(Symbol).where(Symbol.trading_symbol == symbol_name).limit(1)
            )
            sym = result.scalar_one_or_none()
            instrument_key = sym.instrument_key if sym else ""

            parsed_strategy_id = None
            try:
                import json
                strategies = json.loads(strategy)
                if strategies:
                    first_strat = list(strategies.keys())[0]
                    if first_strat.startswith("strategy_"):
                        parsed_strategy_id = int(first_strat.split("_")[1])
                    elif first_strat.isdigit():
                        parsed_strategy_id = int(first_strat)
            except Exception:
                pass

            trade = await mgr.place_order(
                symbol=symbol_name,
                instrument_key=instrument_key,
                direction=signal_type,
                quantity=quantity,
                signal_price=entry_price,
                stop_loss=stop_loss,
                target_price=target_price,
                strategy_id=parsed_strategy_id,
            )

            if trade:
                logger.info(
                    f"[{self.NAME}] 🔴 Live trade placed: {signal_type} {quantity}x {symbol_name} "
                    f"@ ₹{entry_price:.2f}"
                )
                send_telegram_alert(f"Live trade executed: {symbol_name} @ {entry_price:.2f}")
            else:
                logger.warning(f"[{self.NAME}] Live trade placement returned None for {symbol_name}")

        except Exception as e:
            logger.exception(f"[{self.NAME}] Live trade execution failed for {symbol_name}: {e}")

    # ── Main Loop ────────────────────────────────────────────────────────────

    async def run(self):
        """Main worker loop — consume risk-passed signals and execute trades."""
        await asyncio.sleep(1)

        await consume_stream(
            stream=STREAM_SIGNALS_RISK_PASSED,
            group=self.CONSUMER_GROUP,
            consumer=self.CONSUMER_NAME,
            handler=self._handle_signal,
            running=lambda: self.is_running,
        )


# ── Entry Point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    AutoTraderWorker().main()
