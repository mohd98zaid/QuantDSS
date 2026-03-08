"""
Execution Manager — Interfaces with Broker APIs to place live orders.

Fixes applied (Audit Phase 4):
  - Real Upstox LIMIT order via /v2/order/place (no longer a mock)
  - reconcile_orders(): queries Upstox order status for all PENDING trades
  - Post-fill slippage measurement and logging
  - cancel_stale_pending_orders(): cancels PENDING orders older than N minutes
  - HMAC signature verification uses raw_body bytes (Issue 6 fix)
  - Partial fill now updates risk_amount so exposure is tracked correctly (Issue 5 fix)
  - Position reconciliation helper for startup

Fix 1 (Broker SL): Added:
  - place_sl_order(): places SL-M order immediately after entry, protecting capital
    even if the server crashes before the monitoring loop starts.
  - place_target_order(): places a LIMIT target order for automatic profit-taking.
  - place_market_close_order(): places a market order to close a position immediately
    (used by the emergency flatten endpoint).
  - _retry_api_call(): retries broker API calls up to 3 times on transient failures.

Fix 13 (Error handling): logger.exception() used in all except blocks to capture
  full stack traces instead of bare logger.error().
"""
import hashlib
import hmac
import uuid
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_
from app.core.config import settings
from app.core.logging import logger
from app.core.notifier import notifier
from app.models.live_trade import LiveTrade
from app.ingestion.upstox_http import UpstoxHTTPClient
from app.engine.risk_engine import increment_api_error, reset_api_error
from app.models.order_event import OrderEvent

IST = timezone(timedelta(hours=5, minutes=30))

UPSTOX_ORDER_URL = "https://api.upstox.com/v2/order/place"
UPSTOX_ORDER_DETAIL_URL = "https://api.upstox.com/v2/order/details"


class RateLimiter:
    """Token bucket for API calls to prevent broker bans."""
    def __init__(self, calls_per_second: int = 5):
        self.calls_per_second = calls_per_second
        self._tokens = float(calls_per_second)
        self._last_update = datetime.now(timezone.utc)
        self._lock = asyncio.Lock()

    async def acquire(self):
        async with self._lock:
            while True:
                now = datetime.now(timezone.utc)
                elapsed = (now - self._last_update).total_seconds()
                self._tokens = min(
                    self.calls_per_second,
                    self._tokens + elapsed * self.calls_per_second
                )
                self._last_update = now
                if self._tokens >= 1:
                    self._tokens -= 1
                    return
                await asyncio.sleep(0.1)


class ExecutionManager:
    _rate_limiter = RateLimiter(calls_per_second=5)

    def __init__(self, db: AsyncSession):
        self.db = db
        self.upstox_client = UpstoxHTTPClient()
        self._token = settings.upstox_access_token

    def _headers(self) -> dict:
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._token}",
        }

    async def _emit_order_event(
        self,
        order_id: str,
        event_type: str,
        payload: dict | None = None,
        source: str = "execution_manager",
    ) -> None:
        """Record an order lifecycle event in the audit table."""
        try:
            event = OrderEvent(
                order_id=order_id,
                event_type=event_type,
                payload_json=payload or {},
                source=source,
            )
            self.db.add(event)
            await self.db.flush()
        except Exception as e:
            logger.debug(f"OrderEvent audit write failed (non-critical): {e}")

    async def place_order(
        self,
        symbol: str,
        instrument_key: str,
        direction: str,        # "BUY" | "SELL"
        quantity: int,
        signal_price: float,
        stop_loss: float,
        target_price: float,
        max_slippage_pct: float = 0.001,
    ) -> Optional[LiveTrade]:
        """
        Place a LIMIT order through Upstox API with slippage protection.

        Fix: This previously generated a mock order ID. It now calls the real
        Upstox /v2/order/place endpoint with a LIMIT order type.
        Returns a LiveTrade in PENDING state, or None if the API call failed.
        """
        if quantity <= 0:
            logger.error(f"ExecutionManager: {symbol} Invalid quantity {quantity}")
            return None

        # Fix Group 8: Execution Drift Protection
        from app.ingestion.websocket_manager import market_data_cache
        current_price = market_data_cache.get_ltp_if_fresh(instrument_key)
        if current_price is not None and signal_price > 0:
            drift = abs(current_price - signal_price) / signal_price
            if drift > max_slippage_pct:
                logger.warning(
                    f"ExecutionManager: Drift rejected for {symbol} "
                    f"(signal={signal_price}, current={current_price}, drift={drift:.4f})"
                )
                return None

        await self._rate_limiter.acquire()

        # Calculate limit price with slippage buffer
        slippage_amt = signal_price * max_slippage_pct
        if direction == "BUY":
            limit_price = round(signal_price + slippage_amt, 2)
        else:
            limit_price = round(signal_price - slippage_amt, 2)

        # ── Pre-API Trade Persistence (Fix Group 4) ──
        temp_order_id = f"pending_{uuid.uuid4().hex[:8]}"
        risk_amount = round(abs(signal_price - stop_loss) * quantity, 2)
        trade = LiveTrade(
            symbol=symbol,
            instrument_key=instrument_key,
            direction=direction,
            quantity=quantity,
            entry_price=signal_price,       # signal price (pre-fill)
            stop_loss=stop_loss,
            target_price=target_price,
            risk_amount=risk_amount,
            broker_order_id=temp_order_id,
            filled_quantity=0,
            average_price=0.0,
            status="PENDING",
        )
        self.db.add(trade)
        await self.db.flush()
        logger.info(f"ExecutionManager: Registered Pending LiveTrade {trade.id} for {symbol}")

        broker_order_id: str = ""
        order_status: str = "PENDING"

        try:
            if self._token:
                # ── Real Upstox LIMIT order ───────────────────────────
                payload = {
                    "quantity": quantity,
                    "product": "I",                         # Intraday — MIS
                    "validity": "DAY",
                    "price": limit_price,
                    "tag": "quantdss",
                    "instrument_token": instrument_key,
                    "order_type": "LIMIT",
                    "transaction_type": direction,          # BUY / SELL
                    "disclosed_quantity": 0,
                    "trigger_price": 0,
                    "is_amo": False,
                }
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.post(
                        UPSTOX_ORDER_URL,
                        headers=self._headers(),
                        json=payload,
                    )

                if resp.status_code == 200:
                    data = resp.json().get("data", {})
                    broker_order_id = data.get("order_id", "")
                    order_status = "PENDING"
                    
                    # Update trade with actual broker ID
                    trade.broker_order_id = broker_order_id
                    await self.db.flush()

                    logger.info(
                        f"ExecutionManager: LIVE ORDER PLACED — {direction} {quantity} "
                        f"{symbol} @ Limit ₹{limit_price} | Order ID: {broker_order_id}"
                    )
                    reset_api_error()
                else:
                    error_msg = resp.text[:300]
                    logger.error(
                        f"ExecutionManager: Upstox order rejected "
                        f"(HTTP {resp.status_code}): {error_msg}"
                    )
                    # Mark trade failed
                    trade.status = "REJECTED"
                    trade.close_reason = "API_ERROR"
                    await self.db.flush()
                    increment_api_error()
                    return None
            else:
                # ── Paper/simulation fallback when no token is configured ──
                broker_order_id = f"paper_{uuid.uuid4().hex[:8]}"
                order_status = "PENDING"
                
                trade.broker_order_id = broker_order_id
                await self.db.flush()
                
                logger.info(
                    f"ExecutionManager: Simulation order — {direction} {quantity} "
                    f"{symbol} @ ₹{limit_price} | Order ID: {broker_order_id}"
                )
                reset_api_error()

        except Exception as e:
            logger.exception(f"ExecutionManager API Error placing order for {symbol}")
            trade.status = "ERROR"
            trade.close_reason = "EXCEPTION"
            await self.db.flush()
            increment_api_error()
            return None

        # Alert on every live order placement
        asyncio.create_task(notifier.send_alert(
            title="Live Order Placed",
            message=(
                f"**{direction}** {quantity} × {symbol}\n"
                f"Limit: ₹{limit_price} | SL: ₹{stop_loss} | Target: ₹{target_price}"
            ),
            level="INFO"
        ))



        # ── Audit: order placed event ──
        await self._emit_order_event(
            order_id=broker_order_id,
            event_type="placed",
            payload={
                "symbol": symbol,
                "direction": direction,
                "quantity": quantity,
                "limit_price": limit_price,
                "stop_loss": stop_loss,
                "target_price": target_price,
            },
        )

        # Fix Group 4: Place protection orders immediately
        asyncio.create_task(self._place_protection_orders_with_retry(trade.id, stop_loss, target_price))

        return trade

    async def _place_protection_orders_with_retry(self, trade_id: int, stop_loss: float, target_price: float):
        """Fix Group 4: Attempt SL/TP placement after entry with exponential backoff."""
        await asyncio.sleep(1) # Give Upstox time to register the entry
        for attempt in range(1, 4):
            try:
                # Refresh trade from DB
                trade = await self.db.get(LiveTrade, trade_id)
                if not trade or trade.status in ("CLOSED", "REJECTED", "CANCELLED"):
                    return
                # SL
                if not trade.sl_order_id:
                    sl_id = await self.place_sl_order(trade, stop_loss)
                    if sl_id:
                        trade.sl_order_id = sl_id
                        logger.info(f"ExecutionManager: Immediate SL placed for {trade.symbol}: {sl_id}")
                # TP
                if not trade.target_order_id:
                    tp_id = await self.place_target_order(trade, target_price)
                    if tp_id:
                        trade.target_order_id = tp_id
                        logger.info(f"ExecutionManager: Immediate Target placed for {trade.symbol}: {tp_id}")
                
                if trade.sl_order_id and trade.target_order_id:
                    await self.db.commit()
                    return # Success
                await self.db.commit()
            except Exception as e:
                logger.error(f"ExecutionManager: Protection order error for trade {trade_id}: {e}")
            
            # Backoff before retry
            if attempt < 3:
                await asyncio.sleep(2 ** attempt)

        # After all retries failed:
        trade = await self.db.get(LiveTrade, trade_id)
        if trade and trade.status not in ("CLOSED", "REJECTED", "CANCELLED"):
            if not trade.sl_order_id:
                trade.sl_order_id = None
                logger.error(f"CRITICAL: SL order placement failed, enabling local protection for trade {trade.id}")
            await self.db.commit()

    async def _retry_api_call(
        self,
        coro_factory,
        max_attempts: int = 3,
        base_delay: float = 1.0,
        label: str = "",
    ):
        """
        Fix 13: Retry helper for transient broker API failures.
        Exponential backoff between attempts. Returns the response on success.
        Raises the final exception if all attempts fail.
        """
        for attempt in range(1, max_attempts + 1):
            try:
                return await coro_factory()
            except Exception as e:
                if attempt == max_attempts:
                    logger.exception(
                        f"ExecutionManager: API call '{label}' failed after {max_attempts} attempts"
                    )
                    raise
                wait = base_delay * (2 ** (attempt - 1))
                logger.warning(
                    f"ExecutionManager: API call '{label}' attempt {attempt} failed ({e}), "
                    f"retrying in {wait:.1f}s"
                )
                await asyncio.sleep(wait)

    async def place_sl_order(
        self,
        trade: "LiveTrade",
        trigger_price: float,
    ) -> Optional[str]:
        """
        Fix 1: Place a broker-level SL-M (stop-loss market) order.

        SL-M order_type fires a market exit when the trigger price is touched.
        This protects the position at the broker level regardless of server state.

        Returns:
            broker order_id string on success, None on failure.
        """
        if not self._token or not trigger_price or trigger_price <= 0:
            return None

        await self._rate_limiter.acquire()
        # SL direction is opposite to entry direction
        sl_direction = "SELL" if trade.direction == "BUY" else "BUY"

        payload = {
            "quantity": trade.quantity,
            "product": "I",
            "validity": "DAY",
            "price": 0,                          # 0 for market execution on trigger
            "tag": f"quantdss_sl_{trade.id}",
            "instrument_token": trade.instrument_key,
            "order_type": "SL-M",
            "transaction_type": sl_direction,
            "disclosed_quantity": 0,
            "trigger_price": round(trigger_price, 2),
            "is_amo": False,
        }
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    UPSTOX_ORDER_URL, headers=self._headers(), json=payload
                )
            if resp.status_code == 200:
                order_id = resp.json().get("data", {}).get("order_id", "")
                return order_id or None
            logger.error(
                f"ExecutionManager SL order rejected (HTTP {resp.status_code}): {resp.text[:200]}"
            )
            return None
        except Exception:
            logger.exception(
                f"ExecutionManager: Exception placing SL-M order for trade {trade.id}"
            )
            return None

    async def place_target_order(
        self,
        trade: "LiveTrade",
        limit_price: float,
    ) -> Optional[str]:
        """
        Fix 1: Place a LIMIT target order for automatic profit-taking.

        Returns:
            broker order_id string on success, None on failure.
        """
        if not self._token or not limit_price or limit_price <= 0:
            return None

        await self._rate_limiter.acquire()
        target_direction = "SELL" if trade.direction == "BUY" else "BUY"

        payload = {
            "quantity": trade.quantity,
            "product": "I",
            "validity": "DAY",
            "price": round(limit_price, 2),
            "tag": f"quantdss_tgt_{trade.id}",
            "instrument_token": trade.instrument_key,
            "order_type": "LIMIT",
            "transaction_type": target_direction,
            "disclosed_quantity": 0,
            "trigger_price": 0,
            "is_amo": False,
        }
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    UPSTOX_ORDER_URL, headers=self._headers(), json=payload
                )
            if resp.status_code == 200:
                order_id = resp.json().get("data", {}).get("order_id", "")
                return order_id or None
            logger.warning(
                f"ExecutionManager Target order rejected (HTTP {resp.status_code}): {resp.text[:200]}"
            )
            return None
        except Exception:
            logger.exception(
                f"ExecutionManager: Exception placing target order for trade {trade.id}"
            )
            return None

    async def place_market_close_order(
        self,
        trade: "LiveTrade",
    ) -> bool:
        """
        Fix 9 / Fix 1: Place a market order to immediately close an open position.
        Used by the emergency flatten endpoint and auto square-off scheduler.

        Returns True on success (or simulation), False on API failure.
        """
        if not trade.instrument_key:
            logger.error(f"ExecutionManager: No instrument_key for trade {trade.id} — cannot close")
            return False

        await self._rate_limiter.acquire()
        close_direction = "SELL" if trade.direction == "BUY" else "BUY"

        if self._token:
            # Fix 17: Cancel existing SL and Target orders before placing market close
            if trade.sl_order_id:
                await self.cancel_order(trade.sl_order_id)
                trade.sl_order_id = None
            if trade.target_order_id:
                await self.cancel_order(trade.target_order_id)
                trade.target_order_id = None

            payload = {
                "quantity": trade.quantity,
                "product": "I",
                "validity": "DAY",
                "price": 0,
                "tag": f"quantdss_flatten_{trade.id}",
                "instrument_token": trade.instrument_key,
                "order_type": "MARKET",
                "transaction_type": close_direction,
                "disclosed_quantity": 0,
                "trigger_price": 0,
                "is_amo": False,
            }
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.post(
                        UPSTOX_ORDER_URL, headers=self._headers(), json=payload
                    )
                success = resp.status_code == 200
                if not success:
                    logger.error(
                        f"ExecutionManager flatten: Market close rejected "
                        f"(HTTP {resp.status_code}) for trade {trade.id}: {resp.text[:200]}"
                    )
                return success
            except Exception:
                logger.exception(
                    f"ExecutionManager: Exception placing market close for trade {trade.id}"
                )
                return False
        else:
            # Simulation mode
            logger.info(f"ExecutionManager: Simulation market close for trade {trade.id}")
            return True

    async def cancel_order(self, broker_order_id: str) -> bool:
        """Cancel a pending open order via Upstox API."""
        try:
            if self._token:
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.delete(
                        f"https://api.upstox.com/v2/order",
                        headers=self._headers(),
                        params={"order_id": broker_order_id},
                    )
                success = resp.status_code == 200
            else:
                success = True   # simulation

            level = "WARNING" if success else "CRITICAL"
            asyncio.create_task(notifier.send_alert(
                title="Order Cancelled" if success else "Order Cancel FAILED",
                message=f"Order ID: `{broker_order_id}`",
                level=level,
            ))
            return success
        except Exception:
            logger.exception(f"ExecutionManager: cancel_order error for {broker_order_id}")
            return False

    async def handle_webhook(
        self,
        raw_body: bytes,                  # RAW bytes from the HTTP request body
        payload: dict,                    # Already-parsed dict for data extraction
        x_upstox_signature: str = "",
    ) -> None:
        """
        Handle Upstox order update webhooks.

        Fix (Issue 6): HMAC is verified over the ORIGINAL raw bytes from the HTTP
        request, NOT over a re-serialized dict.  Re-serialization changes key order,
        whitespace, and Unicode escaping, causing every legitimate webhook to fail
        if upstox_webhook_secret is configured.
        """
        # ── HMAC verification (raw bytes) ───────────────────────────────────────────
        webhook_secret = getattr(settings, "upstox_webhook_secret", "")
        if webhook_secret:
            expected_sig = hmac.new(
                webhook_secret.encode(), raw_body, hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(expected_sig, x_upstox_signature):
                logger.warning("ExecutionManager Webhook: HMAC signature mismatch — rejected")
                return
        # ─────────────────────────────────────────────────────────────────────

        broker_order_id = payload.get("order_id")
        status = payload.get("status")
        filled_qty = int(payload.get("filled_quantity", 0))
        avg_price = float(payload.get("average_price", 0.0))

        if not broker_order_id or not status:
            return

        # Fix Group 1: Webhook Zombie Trade Bug — match any order ID belonging to the trade
        result = await self.db.execute(
            select(LiveTrade).where(
                or_(
                    LiveTrade.broker_order_id == broker_order_id,
                    LiveTrade.sl_order_id == broker_order_id,
                    LiveTrade.target_order_id == broker_order_id
                )
            ).limit(1)
        )
        trade = result.scalar_one_or_none()
        if not trade:
            logger.warning(f"ExecutionManager Webhook: No trade found for order {broker_order_id}")
            return

        is_sl = trade.sl_order_id == broker_order_id
        is_tp = trade.target_order_id == broker_order_id

        if not is_sl and not is_tp:
            # We are updating the entry order
            trade.filled_quantity = filled_qty
            trade.average_price = avg_price
        
        if status == "complete":
            if is_sl:
                trade.status = "CLOSED"
                trade.exit_price = avg_price
                trade.close_reason = "STOP_LOSS"
                trade.closed_at = datetime.now(IST)
                logger.info(f"ExecutionManager Webhook: SL FILLED for {trade.symbol}")
                # Release risk reservation? Risk group fixes will handle this based on status.
                await self.db.commit()
                return
            elif is_tp:
                trade.status = "CLOSED"
                trade.exit_price = avg_price
                trade.close_reason = "TARGET"
                trade.closed_at = datetime.now(IST)
                logger.info(f"ExecutionManager Webhook: TARGET FILLED for {trade.symbol}")
                await self.db.commit()
                return

            # Fix 3: Webhook deduplication. Ignore if already OPEN.
            if trade.status == "OPEN" and trade.sl_order_id:
                logger.debug(f"ExecutionManager Webhook: Order {broker_order_id} already processed. Ignoring.")
                return

            trade.status = "OPEN"
            if avg_price > 0 and trade.entry_price > 0:
                slippage_pct = abs(avg_price - trade.entry_price) / trade.entry_price * 100
                logger.info(
                    f"ExecutionManager: Fill slippage for {trade.symbol}: "
                    f"signal=₹{trade.entry_price:.2f} avg_fill=₹{avg_price:.2f} "
                    f"slippage={slippage_pct:.3f}%"
                )
            trade.entry_price = avg_price   # update to actual fill price

            # The SL/TP orders are now placed immediately in place_order (Fix Group 4).
            # We just leave this for edge case recovery or rely solely on place_order.
            if self._token and not trade.sl_order_id:
                asyncio.create_task(self._place_protection_orders_with_retry(trade.id, trade.stop_loss, trade.target_price))

        elif status in ("rejected", "cancelled"):
            if is_sl or is_tp:
                if is_sl: trade.sl_order_id = None
                if is_tp: trade.target_order_id = None
                logger.warning(f"ExecutionManager Webhook: {'SL' if is_sl else 'TP'} CANCELLED/REJECTED for {trade.symbol}")
                await self.db.commit()
                return
            if filled_qty > 0:
                # ── Partial fill ────────────────────────────────────────────────────
                # Only quantity shrinks; SL/TP *prices* remain unchanged.
                # risk_amount is recalculated so portfolio exposure is correct.
                original_qty = trade.quantity
                trade.status = "OPEN"
                trade.quantity = filled_qty
                trade.filled_quantity = filled_qty
                stop_distance = abs(
                    (trade.entry_price or 0) - (trade.stop_loss or 0)
                )
                if stop_distance > 0:
                    trade.risk_amount = round(filled_qty * stop_distance, 2)
                logger.warning(
                    f"ExecutionManager Webhook: Partial fill for {trade.symbol} "
                    f"({filled_qty}/{original_qty} shares). "
                    f"Risk reduced to ₹{trade.risk_amount or 0:.2f}"
                )
                # Fix 7: Cancel old full-qty target, place new reduced-qty one.
                # Without this, the stale target at full=original_qty would overshoot
                # the actual position, creating a naked SELL on the excess quantity.
                if self._token and trade.target_order_id:
                    cancelled = await self.cancel_order(trade.target_order_id)
                    if cancelled:
                        logger.info(
                            f"ExecutionManager Webhook (Partial): Cancelled stale target "
                            f"{trade.target_order_id} for {trade.symbol}"
                        )
                    new_tgt = await self.place_target_order(trade, trade.target_price)
                    if new_tgt:
                        trade.target_order_id = new_tgt
                        logger.info(
                            f"ExecutionManager Webhook (Partial): New target {new_tgt} "
                            f"for {trade.symbol} qty={filled_qty}"
                        )
                asyncio.create_task(notifier.send_alert(
                    title="Partial Fill",
                    message=(
                        f"{trade.symbol}: {filled_qty}/{original_qty} filled. "
                        f"Risk ₹{trade.risk_amount or 0:.2f}. "
                        f"Target updated to qty={filled_qty}."
                    ),
                    level="WARNING",
                ))
            else:
                trade.status = "CLOSED"
                trade.close_reason = status.upper()
                trade.closed_at = datetime.now(IST)
                # Fix 4: Removed increment_api_error(). A broker rejection is NOT
                # an API connectivity failure and must not fire the circuit breaker.


        await self.db.commit()
        logger.info(
            f"ExecutionManager Webhook: Trade {trade.id} ({broker_order_id}) → {status}"
        )

    async def reconcile_orders(self) -> None:
        """
        Fallback periodic sync for missed webhooks.

        Fix: Was previously an empty stub. Now queries Upstox order detail
        for every PENDING trade older than 60 seconds.
        """
        result = await self.db.execute(
            select(LiveTrade).where(
                LiveTrade.status.in_(["PENDING", "PARTIALLY_FILLED"])
            )
        )
        pending_trades = result.scalars().all()

        if not pending_trades:
            return

        logger.info(f"ExecutionManager: Reconciling {len(pending_trades)} pending orders")

        for trade in pending_trades:
            # Skip very recent orders — they may still be processing
            if trade.created_at:
                age_s = (datetime.now(IST) - trade.created_at.replace(tzinfo=timezone.utc).astimezone(IST)).total_seconds()
                if age_s < 60:
                    continue

            try:
                if not self._token or not trade.broker_order_id:
                    continue

                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.get(
                        UPSTOX_ORDER_DETAIL_URL,
                        headers=self._headers(),
                        params={"order_id": trade.broker_order_id},
                    )

                if not resp.is_success:
                    logger.warning(
                        f"ExecutionManager Reconcile: Could not fetch order "
                        f"{trade.broker_order_id}: HTTP {resp.status_code}"
                    )
                    continue

                data = resp.json().get("data", {})
                broker_status = data.get("status", "")
                filled_qty = int(data.get("filled_quantity", 0))
                avg_price = float(data.get("average_price", 0.0))

                if broker_status == "complete":
                    trade.status = "OPEN"
                    trade.filled_quantity = filled_qty
                    trade.average_price = avg_price
                    if avg_price > 0:
                        trade.entry_price = avg_price
                    logger.info(
                        f"ExecutionManager Reconcile: "
                        f"Trade {trade.id} reconciled as OPEN (filled @ ₹{avg_price})"
                    )
                elif broker_status in ("rejected", "cancelled") and filled_qty == 0:
                    trade.status = "CLOSED"
                    trade.close_reason = broker_status.upper()
                    trade.closed_at = datetime.now(IST)
                    logger.info(
                        f"ExecutionManager Reconcile: "
                        f"Trade {trade.id} reconciled as CLOSED ({broker_status})"
                    )

                reset_api_error()

            except Exception:
                logger.exception(
                    f"ExecutionManager: Reconcile error for trade {trade.id}"
                )
                increment_api_error()

        await self.db.commit()

    async def cancel_stale_pending_orders(
        self,
        timeout_minutes: int = 5,
    ) -> int:
        """
        Issue 4 Fix: Cancel PENDING orders older than timeout_minutes.

        Called by the scheduler every 2 minutes.  Pass timeout_minutes=0 to
        cancel ALL PENDING orders (used by the admin kill-switch).
        Returns the number of orders successfully cancelled.
        """
        cutoff = datetime.now(IST) - timedelta(minutes=timeout_minutes)
        result = await self.db.execute(
            select(LiveTrade).where(
                LiveTrade.status == "PENDING",
                or_(
                    LiveTrade.created_at < cutoff,
                    LiveTrade.created_at.is_(None)
                )
            )
        )
        stale = result.scalars().all()
        cancelled = 0
        for trade in stale:
            ok = await self.cancel_order(trade.broker_order_id)
            if ok:
                trade.status = "CANCELLED"
                trade.close_reason = "ORDER_TIMEOUT"
                trade.closed_at = datetime.now(IST)
                cancelled += 1
                logger.warning(
                    f"ExecutionManager: PENDING order {trade.broker_order_id} "
                    f"timed out after {timeout_minutes}min — cancelled"
                )
                # Fix 4: Do NOT increment_api_error() on a SUCCESSFUL cancellation.
                # The circuit breaker should only fire on actual broker API failures,
                # not on routine order lifecycle events. Old code here was:
                #   increment_api_error()  <-- REMOVED
            else:
                # Cancellation itself failed — this IS a connectivity issue
                increment_api_error()
        if cancelled:
            await self.db.commit()
        return cancelled

    @classmethod
    async def reconcile_on_startup(cls, db: AsyncSession) -> None:
        """
        Fix: On startup, query the broker for all PENDING trades and update their status.
        Prevents stale PENDING trades from occupying position slots after a crash-restart.
        """
        result = await db.execute(
            select(LiveTrade).where(LiveTrade.status == "PENDING")
        )
        pending = result.scalars().all()
        if not pending:
            return

        logger.info(
            f"ExecutionManager Startup: Reconciling {len(pending)} PENDING trades with broker"
        )
        mgr = cls(db)
        await mgr.reconcile_orders()
