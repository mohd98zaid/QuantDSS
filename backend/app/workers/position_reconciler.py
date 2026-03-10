"""
Position Reconciler Worker

Runs every 60 seconds.
Purpose: Detect mismatches between the live broker position state and the local database state.
"""
import asyncio
from datetime import datetime, timezone
from sqlalchemy import select

from app.core.database import async_session_factory
from app.core.logging import logger
from app.core.notifier import notifier
from app.models.live_trade import LiveTrade
from app.ingestion.broker_manager import get_broker_client

class PositionReconcilerWorker:
    """
    Periodically fetches open positions from the broker API and compares
    them to the local database to detect and auto-correct any drift out of sync.
    """

    NAME = "position-reconciler"

    async def fetch_broker_positions(self):
        """Mock/Wrapper for fetching live broker positions."""
        try:
            client = get_broker_client()
            if hasattr(client, 'get_positions'):
                return await client.get_positions()
            # If the specific broker adapter lacks get_positions, log and return empty
            return []
        except Exception as e:
            logger.error(f"[{self.NAME}] Error fetching broker positions: {e}")
            return []

    async def reconcile(self):
        """
        Executes the reconciliation logic between DB and Broker.
        """
        async with async_session_factory() as db:
            try:
                # 1. Fetch broker positions
                broker_positions = await self.fetch_broker_positions()
                broker_map = {}
                for bp in broker_positions:
                    # Expecting dict like: {'symbol': 'RELIANCE', 'quantity': 50, 'direction': 'LONG'}
                    symbol = bp.get("symbol")
                    if symbol:
                        broker_map[symbol] = bp
                        
                # 2. Fetch DB open trades
                result = await db.execute(
                    select(LiveTrade).where(LiveTrade.status == 'OPEN')
                )
                open_trades = result.scalars().all()
                db_map = {t.symbol: t for t in open_trades if t.symbol}

                # 3. Compare states
                
                # Check DB trades against Broker
                for symbol, trade in db_map.items():
                    broker_state = broker_map.get(symbol)
                    
                    if not broker_state:
                        # Case 2: DB shows trade but broker closed -> mark CLOSED
                        logger.warning(f"[{self.NAME}] DISCREPANCY: DB has OPEN trade for {symbol} but broker has closed it. Marking CLOSED.")
                        trade.status = 'CLOSED'
                        trade.exit_time = datetime.now(timezone.utc)
                    else:
                        # Case 3: Quantity mismatch -> correct quantity
                        if trade.quantity != broker_state.get("quantity"):
                            logger.warning(
                                f"[{self.NAME}] DISCREPANCY: Quantity mismatch for {symbol}. "
                                f"DB: {trade.quantity}, Broker: {broker_state.get('quantity')}. Correcting DB."
                            )
                            trade.quantity = broker_state.get("quantity", trade.quantity)

                # Check Broker against DB
                for symbol, bp in broker_map.items():
                    if symbol not in db_map:
                        # Fix Group 3: Zombie broker position detected - DO NOT ADOPT, FORCE CLOSE
                        qty = bp.get("quantity", 0)
                        if qty == 0:
                            continue

                        logger.error(f"[{self.NAME}] Zombie broker position detected: {symbol} ({qty})")
                        try:
                            from app.engine.execution_manager import ExecutionManager
                            from app.models.symbol import Symbol
                            dr = bp.get("direction", "LONG" if qty > 0 else "SHORT")

                            # ── Step 1: Primary key resolution (DB Symbol table) ──────
                            result_sym = await db.execute(
                                select(Symbol).where(Symbol.trading_symbol == symbol).limit(1)
                            )
                            sym_info = result_sym.scalar_one_or_none()
                            instrument_key = sym_info.instrument_key if sym_info else ""

                            # ── Step 2: Secondary key resolution (Fix Group 5 / V-06) ─
                            # If primary lookup failed, try Upstox instrument master API.
                            if not instrument_key:
                                try:
                                    from app.ingestion.upstox_http import UpstoxHTTPClient
                                    broker_http = UpstoxHTTPClient()
                                    resolved_key = await broker_http.get_instrument_key_by_symbol(symbol)
                                    if resolved_key:
                                        instrument_key = resolved_key
                                        logger.info(
                                            f"[{self.NAME}] Resolved instrument_key for zombie {symbol} "
                                            f"via Upstox instrument master: {instrument_key}"
                                        )
                                except Exception as ik_exc:
                                    logger.error(
                                        f"[{self.NAME}] Secondary instrument_key lookup failed for {symbol}: {ik_exc}"
                                    )

                            # ── Step 3: If still unresolvable → halt + alert, never skip ─
                            if not instrument_key:
                                logger.critical(
                                    f"[{self.NAME}] CRITICAL: Cannot resolve instrument_key for zombie "
                                    f"position {symbol} (qty={qty}). Halting new signals. "
                                    f"Manual broker intervention required."
                                )
                                try:
                                    from app.models.daily_risk_state import DailyRiskState
                                    import datetime as _dt
                                    today = _dt.date.today()
                                    async with async_session_factory() as halt_db:
                                        rs_result = await halt_db.execute(
                                            select(DailyRiskState).where(
                                                DailyRiskState.trade_date == today
                                            )
                                        )
                                        risk_state = rs_result.scalar_one_or_none()
                                        if risk_state and not risk_state.is_halted:
                                            risk_state.is_halted = True
                                            risk_state.halt_reason = (
                                                f"Zombie position {symbol}: instrument_key unresolvable. "
                                                f"Manual intervention required."
                                            )
                                            await halt_db.commit()
                                            logger.critical(
                                                f"[{self.NAME}] DailyRiskState.is_halted set to True "
                                                f"due to unresolvable zombie position for {symbol}"
                                            )
                                except Exception as halt_exc:
                                    logger.error(
                                        f"[{self.NAME}] Failed to set halt state for zombie {symbol}: {halt_exc}"
                                    )
                                asyncio.create_task(notifier.send_alert(
                                    title="CRITICAL: Zombie Position Unresolvable",
                                    message=(
                                        f"Zombie broker position **{symbol}** (qty={qty}) could not be closed.\n"
                                        f"instrument_key unresolvable from DB and Upstox master.\n"
                                        f"**Trading halted. Manual broker intervention required.**"
                                    ),
                                    level="CRITICAL",
                                ))
                                from app.alerts.telegram_notifier import send_telegram_alert
                                send_telegram_alert(f"🚨 CRITICAL: Zombie Position Unresolvable\nSymbol: {symbol} (qty={qty})")
                                continue  # do not proceed further for this symbol

                            # ── Step 4: Cancel orphaned orders then market-close position ─
                            mgr = ExecutionManager(db)
                            try:
                                from app.ingestion.upstox_http import UpstoxHTTPClient
                                broker_http = UpstoxHTTPClient()
                                open_orders = await broker_http.get_open_orders()
                                for order in open_orders:
                                    if order.get("instrument_token") == instrument_key:
                                        order_id = order.get("order_id")
                                        if order_id:
                                            logger.info(f"[{self.NAME}] Cancelling orphaned order {order_id} for {symbol}")
                                            await mgr.cancel_order(order_id)
                            except Exception as oc_e:
                                logger.error(f"[{self.NAME}] Error cancelling orphaned orders for {symbol}: {oc_e}")

                            dummy_trade = LiveTrade(
                                symbol=symbol,
                                instrument_key=instrument_key,
                                quantity=abs(qty),
                                direction=dr
                            )
                            success = await mgr.place_market_close_order(dummy_trade)
                            if success:
                                logger.info(f"[{self.NAME}] Successfully closed zombie position for {symbol}")
                            else:
                                logger.error(f"[{self.NAME}] Market close failed for zombie {symbol}")

                        except Exception as e:
                            logger.exception(f"[{self.NAME}] Failed to close zombie position for {symbol}: {e}")

                await db.commit()
            except Exception as e:
                logger.error(f"[{self.NAME}] Reconciliation iteration failed: {e}")

    async def run(self):
        """Main loop."""
        logger.info(f"[{self.NAME}] Started. Reconciling every 60s.")
        while True:
            await asyncio.sleep(60)
            logger.debug(f"[{self.NAME}] Running reconciliation cycle...")
            await self.reconcile()

if __name__ == "__main__":
    import uvloop
    uvloop.install()
    worker = PositionReconcilerWorker()
    asyncio.run(worker.run())
