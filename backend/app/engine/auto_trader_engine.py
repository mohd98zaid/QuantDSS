"""
Auto-Trader Engine — two modes of operation:

1. REACTIVE  (primary): hooks into every scanner bulk/single scan.
   When the scanner finds BUY/SELL, this engine immediately executes
   a paper trade — zero latency, driven by real scanner results.
   It also auto-closes reverse positions (SELL closes open BUY, etc.).

2. SCHEDULED (backup): APScheduler runs every N minutes and scans
   the watchlist independently, so auto-trading continues even when
   no one is manually using the scanner.

Fixes applied (Audit Phase 6):
  - Unified position sizing: _calc_qty() is now only a FALLBACK. When the
    risk engine has already computed a quantity (via RiskDecision), that takes
    priority. This eliminates the dual-sizing-system audit gap.
  - Weekly P&L is fetched from the last 7 calendar days and passed to Portfolio
    so the WeeklyLossCircuitBreaker rule can function (Issue 8 fix).
  - Emergency halt: auto-trader checks is_halted on DailyRiskState before execution.

Fix 8 (Strategy Health Monitor):
  - _close_trade() now calls strategy_health_monitor.record_trade_async(strategy_id, pnl, db)
    so every trade close is tracked for health monitoring and automatic strategy disabling.

Fix 11 (Session Management):
  - auto_square_off() closes all open positions at 15:15 IST.
    Should be wired to APScheduler with a cron trigger at 15:15 IST.
"""
from datetime import date as _date, datetime, timezone, timedelta
from typing import Optional, Any
from dataclasses import dataclass
import asyncio

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_session_factory
from app.core.logging import logger
from app.core.redis_lock import redis_lock
from app.engine.trading_mode import TradingModeController, TradingMode
from app.models.auto_trade_config import AutoTradeConfig
from app.models.auto_trade_log import AutoTradeLog
from app.models.paper_trade import PaperTrade
from app.models.live_trade import LiveTrade
from app.models.risk_config import RiskConfig
from app.engine.execution_manager import ExecutionManager

IST = timezone(timedelta(hours=5, minutes=30))


def _is_market_hours() -> bool:
    now = datetime.now(IST)
    if now.weekday() >= 5:
        return False
    h, m = now.hour, now.minute
    # Fix 18: Hard cutoff at 14:45 IST for entries
    # Ensures enough time for trade lifecycle before 15:15 square-off
    return (h > 9 or (h == 9 and m >= 15)) and (h < 14 or (h == 14 and m <= 45))


async def _get_config(db: AsyncSession) -> Optional[AutoTradeConfig]:
    result = await db.execute(select(AutoTradeConfig).limit(1))
    return result.scalar_one_or_none()


async def _count_open_trades(db: AsyncSession) -> int:
    """Count all open positions (paper + live) to correctly enforce max_open_positions."""
    from app.models.live_trade import LiveTrade
    result_paper = await db.execute(
        select(func.count()).select_from(PaperTrade).where(PaperTrade.status == "OPEN")
    )
    result_live = await db.execute(
        select(func.count()).select_from(LiveTrade).where(LiveTrade.status == "OPEN")
    )
    paper_count = result_paper.scalar_one() or 0
    live_count  = result_live.scalar_one()  or 0
    return paper_count + live_count


async def _load_weekly_pnl(db: AsyncSession) -> float:
    """
    Issue 8 Fix: Sum realised_pnl from DailyRiskState for the rolling 7-day window.

    This is called once per auto-trader cycle to populate Portfolio.weekly_realised_pnl
    so that WeeklyLossCircuitBreaker can enforce the weekly cap even after a restart.
    """
    from datetime import date, timedelta as _td
    from app.models.daily_risk_state import DailyRiskState
    cutoff = date.today() - _td(days=7)
    try:
        result = await db.execute(
            select(func.sum(DailyRiskState.realised_pnl)).where(
                DailyRiskState.trade_date >= cutoff
            )
        )
        val = result.scalar_one_or_none()
        return float(val or 0.0)
    except Exception as e:
        logger.warning(f"AutoTrader: Could not load weekly PnL (non-fatal): {e}")
        return 0.0


async def _load_portfolio(db: AsyncSession, risk_cfg=None) -> "Portfolio":
    """
    Fix 2: Load open trade data from DB to correctly populate Portfolio for risk engine.

    Without this, GrossExposureFilter always passes (open_position_values=[]) and
    PositionSizer ignores existing risk (committed_risk=0). This caused the system
    to potentially deploy 10x its capital with no portfolio-level exposure control.
    """
    from app.engine.risk_engine import Portfolio
    from app.models.paper_trade import PaperTrade
    from app.models.live_trade import LiveTrade

    current_balance = float(risk_cfg.paper_balance) if risk_cfg else 100_000.0
    peak_balance = current_balance

    try:
        # Load open paper trades (we're always in paper context here)
        result_p = await db.execute(
            select(PaperTrade).where(PaperTrade.status == "OPEN")
        )
        open_paper = result_p.scalars().all()

        result_l = await db.execute(
            select(LiveTrade).where(LiveTrade.status == "OPEN")
        )
        open_live = result_l.scalars().all()

        all_open = list(open_paper) + list(open_live)

        open_position_values = [
            float(t.entry_price or 0) * int(t.quantity or 0)
            for t in all_open
        ]
        committed_risk = sum(
            float(getattr(t, "risk_amount", None) or 0)
            for t in all_open
        )
        open_symbols = [t.symbol for t in all_open]

        return Portfolio(
            current_balance=current_balance,
            peak_balance=peak_balance,
            open_positions=len(all_open),
            open_symbols=open_symbols,
            open_position_values=open_position_values,
            committed_risk=committed_risk,
        )
    except Exception as e:
        logger.warning(f"AutoTrader: _load_portfolio failed (non-fatal): {e}")
        return Portfolio(current_balance=current_balance, peak_balance=peak_balance)



def _add_log(db: AsyncSession, trading_mode: str | None = None, **kwargs) -> None:
    db.add(AutoTradeLog(trading_mode=trading_mode, **kwargs))


def _calc_qty(cfg: AutoTradeConfig, entry_price: float, risk_engine_qty: int | None = None) -> int:
    """
    Calculate trade quantity.
    Fix: If risk engine has already computed a quantity (from PositionSizer rule),
    that takes priority. Falls back to AutoTradeConfig simple sizing only when
    risk engine hasn't run (e.g., scanner reactive mode without a full pipeline).
    """
    if risk_engine_qty is not None and risk_engine_qty > 0:
        return risk_engine_qty   # Risk engine sizing takes priority
    if cfg.sizing_mode == "capital":
        if entry_price <= 0:
            return 0
        return int(cfg.capital_per_trade // entry_price)
    else:
        return cfg.qty_per_trade


# ──────────────────────────────────────────────────────────────────────────────
# SIGNAL-BASED EXIT — close reverse positions automatically
# ──────────────────────────────────────────────────────────────────────────────

async def _close_trade(
    db: AsyncSession,
    cfg: AutoTradeConfig,
    symbol: str,
    signal: str,
    exit_price: float,
    strategy: str,
    timeframe: str,
    risk_reward: float = 0.0,
    rsi: float | None = None,
    trend: str | None = None,
    strategy_id: int | None = None,   # Fix 8: required for health monitor
) -> tuple[bool, float | None]:
    """Close an existing position if signal is a reversal.
    Returns (was_closed, realized_pnl_or_None).
    BUY signal closes open SELL positions; SELL signal closes open BUY positions."""
    opposite = "SELL" if signal == "BUY" else "BUY"
    # TradingModeController: determine which trade table to query
    route = TradingModeController.route_execution(cfg)
    trade_model = LiveTrade if route == "live" else PaperTrade
    result = await db.execute(
        select(trade_model).where(
            trade_model.symbol == symbol,
            trade_model.status == "OPEN",
            trade_model.direction == opposite,
        )
    )
    trade = result.scalar_one_or_none()
    if not trade:
        return False, None

    if exit_price <= 0:
        return False, None

    trade.status = "CLOSED"
    trade.exit_price = exit_price
    trade.closed_at = datetime.now(IST)
    trade.close_reason = "SIGNAL_EXIT"
    multiplier = 1 if trade.direction == "BUY" else -1
    
    # Fix Group 7: Net PnL (deduct estimated slippage and fees)
    gross_pnl = (exit_price - trade.entry_price) * trade.quantity * multiplier
    cost = 40.0 + (trade.entry_price * trade.quantity * 0.0005)
    trade.realized_pnl = gross_pnl - cost
    closed_pnl = float(trade.realized_pnl)

    # Return margin + PnL to balance (paper only)
    if route == "paper":
        bal_result = await db.execute(select(RiskConfig).limit(1))
        risk_cfg = bal_result.scalar_one_or_none()
        if risk_cfg:
            margin_returned = (trade.quantity * trade.entry_price) / 5
            risk_cfg.paper_balance = float(risk_cfg.paper_balance) + margin_returned + trade.realized_pnl
    else:
        # For live trading, rely on broker's real balance
        pass

    mode_val = TradingModeController.get_mode(cfg).value
    _add_log(db, trading_mode=mode_val,
             symbol=symbol, signal=signal, action="CLOSE",
             reason=f"Reverse signal EXIT ({opposite} → {signal})",
             entry_price=trade.entry_price, stop_loss=trade.stop_loss,
             target_price=trade.target_price, risk_reward=risk_reward,
             rsi=rsi, trend=trend,
             strategy=strategy, timeframe=timeframe,
             trade_id=trade.id)

    logger.info(
        f"AutoTrader {TradingModeController.log_prefix(cfg)}: CLOSED {trade.direction} {symbol} @ ₹{exit_price:.2f} "
        f"(SIGNAL_EXIT) PnL=₹{trade.realized_pnl:.2f}"
    )

    # Fix 8: Record trade with strategy health monitor
    _sid = strategy_id or getattr(trade, "strategy_id", None)
    if _sid is not None and trade.realized_pnl is not None:
        try:
            from app.engine.strategy_health import strategy_health_monitor
            await strategy_health_monitor.record_trade_async(_sid, float(trade.realized_pnl), db)
        except Exception:
            logger.exception(
                f"AutoTrader: Failed to record trade health for strategy {_sid} (non-fatal)"
            )

    return True, closed_pnl


# ──────────────────────────────────────────────────────────────────────────────
# ENTRY — open new paper trades
# ──────────────────────────────────────────────────────────────────────────────

async def _open_trade(
    db: AsyncSession,
    cfg: AutoTradeConfig,
    symbol: str,
    signal: str,
    entry_price: float,
    stop_loss: float,
    target_price: float,
    risk_state: Any,          # Added for Fix 2
    portfolio: Any,           # Added for Fix 2
    instrument_key: str = "",
    risk_reward: float = 0.0,
    rsi: float | None = None,
    trend: str | None = None,
    strategy: str = "",
    timeframe: str = "",
    risk_engine_qty: int | None = None,  # Qty from RiskEngine (if pre-validated)
    signal_id: str = "",                 # Fix 2: Passes down idempotency key
) -> bool:
    """
    Open a trade for the given signal, branching for PAPER vs LIVE execution.
    All validation (dupe check, balance check, sizing) is done here.

    If risk_engine_qty is provided, the signal has already been validated by
    the Risk Engine via FinalAlertGenerator and we skip re-validation.
    """
    if signal not in ("BUY", "SELL"):
        return False

    # ── TradingModeController: DISABLED check ─────────────────────────────────
    route = TradingModeController.route_execution(cfg)
    mode_val = TradingModeController.get_mode(cfg).value

    if route is None:
        # Mode is DISABLED — drop the signal
        TradingModeController.log_disabled_drop(symbol, signal, "DISABLED mode — no trade")
        _add_log(db, trading_mode=mode_val,
                 symbol=symbol, signal=signal, action="DISABLED_DROP",
                 reason="TradingMode=DISABLED",
                 strategy=strategy, timeframe=timeframe,
                 entry_price=entry_price, stop_loss=stop_loss,
                 target_price=target_price)
        return False

    if entry_price <= 0:
        logger.debug(f"AutoTrader {symbol} rejected: entry_price <= 0 ({entry_price})")
        _add_log(db, trading_mode=mode_val,
                 symbol=symbol, signal=signal, action="SKIP",
                 reason=f"Invalid entry price: {entry_price}",
                 strategy=strategy, timeframe=timeframe,
                 stop_loss=stop_loss, target_price=target_price,
                 risk_reward=risk_reward, rsi=rsi, trend=trend)
        return False

    # Calculate quantity based on sizing mode
    qty = _calc_qty(cfg, entry_price, risk_engine_qty)
    if qty < 1:
        logger.debug(f"AutoTrader {symbol} rejected: calc qty < 1 (Price {entry_price}, Capital limit: {cfg.capital_per_trade})")
        _add_log(db, trading_mode=mode_val,
                 symbol=symbol, signal=signal, action="SKIP",
                 reason=f"Price ₹{entry_price:.2f} exceeds capital limit ₹{cfg.capital_per_trade:.0f}",
                 strategy=strategy, timeframe=timeframe,
                 entry_price=entry_price, stop_loss=stop_loss,
                 target_price=target_price, risk_reward=risk_reward,
                 rsi=rsi, trend=trend)
        return False

    # Duplicate position check
    trade_model = LiveTrade if route == "live" else PaperTrade
    dupe = await db.execute(
        select(trade_model).where(
            trade_model.symbol == symbol,
            trade_model.status == "OPEN",
        )
    )
    if dupe.scalar_one_or_none():
        logger.debug(f"AutoTrader {symbol} rejected: Duplicate OPEN position.")
        _add_log(db, trading_mode=mode_val,
                 symbol=symbol, signal=signal, action="SKIP",
                 reason="Already has an open position",
                 strategy=strategy, timeframe=timeframe,
                 entry_price=entry_price, stop_loss=stop_loss,
                 target_price=target_price, risk_reward=risk_reward,
                 rsi=rsi, trend=trend)
        return False

    # ── Balance check (paper only) ─────────────────────────────────────────────
    bal_result = await db.execute(select(RiskConfig).limit(1))
    risk_cfg_model = bal_result.scalar_one_or_none()

    margin_req = (qty * entry_price) / 5
    if route == "paper":
        if risk_cfg_model and float(risk_cfg_model.paper_balance) < margin_req:
            print(f"DEBUG AutoTrader {symbol} rejected: Insufficient Balance. (need {margin_req}, have {risk_cfg_model.paper_balance})")
            _add_log(db, trading_mode=mode_val,
                     symbol=symbol, signal=signal, action="SKIP",
                     reason=f"Insufficient paper balance (need ₹{margin_req:.0f})",
                     strategy=strategy, timeframe=timeframe,
                     entry_price=entry_price, stop_loss=stop_loss,
                     target_price=target_price, risk_reward=risk_reward,
                     rsi=rsi, trend=trend)
            return False

    # ── Open the trade ─────────────────────────────────────────────────────────
    if route == "live":
        # LIVE EXECUTION BLOCK
        exec_mgr = ExecutionManager(db)
        trade = await exec_mgr.place_order(
            symbol=symbol,
            instrument_key=instrument_key,
            direction=signal,
            quantity=qty,
            signal_price=entry_price,
            stop_loss=stop_loss,
            target_price=target_price,
            max_slippage_pct=cfg.max_slippage_pct,
            signal_id=signal_id,
        )
        if trade:
            logger.info(f"DEBUG AutoTrader {symbol} LIVE order created: ID {trade.id} PENDING.")
        else:
            print(f"DEBUG AutoTrader {symbol} LIVE order failed: ExecutionManager returned None.")
            return False
    else:
        # PAPER EXECUTION BLOCK
        trade = PaperTrade(
            symbol=symbol,
            instrument_key=instrument_key,
            direction=signal,
            quantity=qty,
            entry_price=entry_price,
            stop_loss=stop_loss,
            target_price=target_price,
            status="OPEN",
            trading_mode=mode_val,
        )
        db.add(trade)
    
        if risk_cfg_model:
            risk_cfg_model.paper_balance = float(risk_cfg_model.paper_balance) - margin_req

    await db.flush()  # get trade.id

    _add_log(db, trading_mode=mode_val,
             symbol=symbol, signal=signal, action="OPEN",
             reason=f"Signal from scanner (qty={qty})",
             entry_price=entry_price,
             stop_loss=stop_loss,
             target_price=target_price,
             risk_reward=risk_reward,
             rsi=rsi,
             trend=trend,
             strategy=strategy, timeframe=timeframe,
             trade_id=trade.id)

    logger.info(
        f"AutoTrader {TradingModeController.log_prefix(cfg)}: OPENED {signal} {symbol} @ ₹{entry_price:.2f} "
        f"SL=₹{stop_loss:.2f} TP=₹{target_price:.2f} "
        f"R:R={risk_reward:.1f} RSI={rsi} Trend={trend} qty={qty}"
    )
    return True


# ──────────────────────────────────────────────────────────────────────────────
# BACKGROUND QUEUE — Decouple scanner from execution
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class SignalPayload:
    results: list[Any]
    strategy: str
    timeframe: str

class AutoTraderQueue:
    _instance = None

    def __init__(self):
        # Fix 14: Bounded queue prevents unbounded memory growth during fast scanning.
        # At maxsize=100, a full queue causes enqueue() to log a warning and skip
        # the oldest pending payload rather than growing without limit.
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._worker_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()

    @classmethod
    def get_instance(cls) -> "AutoTraderQueue":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def start_worker(self) -> None:
        """
        Fix 14: Start the background worker at application startup (not lazily).
        Must be called from main.py lifespan after the event loop is running.
        This prevents the race condition where the queue is created on a different
        event loop than the one that will run the worker.
        """
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._worker_loop())
            logger.info("AutoTrader Queue: worker started at startup")

    async def enqueue(self, results: list[Any], strategy: str, timeframe: str):
        # Fix 14: Start worker on first enqueue as fallback (in case start_worker
        # was not called at startup), but log a warning.
        async with self._lock:
            if self._worker_task is None or self._worker_task.done():
                logger.warning(
                    "AutoTrader Queue: worker not started at startup — starting now. "
                    "Call autotrader_queue.start_worker() in main.py lifespan."
                )
                self._worker_task = asyncio.create_task(self._worker_loop())
        try:
            self.queue.put_nowait(SignalPayload(results, strategy, timeframe))
        except asyncio.QueueFull:
            logger.warning(
                f"AutoTrader Queue: FULL (maxsize=100) — dropping oldest payload "
                f"for strategy={strategy} to prevent memory growth"
            )
            # Drop oldest, then add new
            try:
                self.queue.get_nowait()
                self.queue.put_nowait(SignalPayload(results, strategy, timeframe))
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                pass

    async def _worker_loop(self):
        logger.info("AutoTrader Queue worker started.")
        while True:
            try:
                payload = await self.queue.get()
                await _process_batch(payload.results, payload.strategy, payload.timeframe)
                self.queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("AutoTrader Queue Worker Error")

# Global instance — created at module import but worker started at startup
autotrader_queue = AutoTraderQueue.get_instance()

# ──────────────────────────────────────────────────────────────────────────────
# PUBLIC API — called by scanner endpoints
# ──────────────────────────────────────────────────────────────────────────────

async def process_scanner_results(results: list[Any], strategy: str, timeframe: str) -> None:
    """
    REACTIVE MODE — ENQUEUE signals from scanner endpoints.
    Returns immediately, decoupling the scanner HTTP request from the DB execution.
    """
    await autotrader_queue.enqueue(results, strategy, timeframe)

async def _process_batch(results: list[Any], strategy: str, timeframe: str) -> None:
    """
    Internal worker method to process a batch of signals.

    `results` is a list of BulkScanResult objects with fields:
        symbol, signal, entry_price, stop_loss, target_price
    """
    async with async_session_factory() as db:
        cfg = await _get_config(db)
        if not cfg or not cfg.enabled:
            logger.warning(
                "AutoTrader (reactive): auto-trader is DISABLED — signals will not be executed. "
                "Go to Auto-Trader → Settings and enable it, or call PUT /api/v1/auto-trader/config "
                "with enabled=true."
            )
            return  # auto-trader disabled — nothing to do

        # Fix 6: Check halt flag before processing any signals from the queue.
        # Signals enqueued before halt was set must NOT be executed.
        from app.models.daily_risk_state import DailyRiskState
        today = _date.today()
        risk_state_result = await db.execute(
            select(DailyRiskState).where(DailyRiskState.trade_date == today)
        )
        today_risk_state = risk_state_result.scalar_one_or_none()
        if today_risk_state and today_risk_state.is_halted:
            logger.warning(
                "AutoTrader (reactive): DailyRiskState.is_halted=True — "
                "draining queue without processing signals"
            )
            return

        portfolio = await _load_portfolio(db)
        portfolio.weekly_realised_pnl = await _load_weekly_pnl(db)

        open_count = await _count_open_trades(db)
        slots_left = cfg.max_open_positions - open_count
        print(f"DEBUG process_scanner_results: Total results: {len(results)}. Open count: {open_count}, Slots left: {slots_left}")

        opened = 0
        closed = 0
        for r in results:
            signal = getattr(r, "signal", None)
            if signal not in ("BUY", "SELL"):
                continue

            # ── 1. Try to close a reverse position first (no lock needed) ──
            was_closed, closed_pnl = await _close_trade(
                db, cfg, r.symbol, signal,
                exit_price=float(r.entry_price or 0),
                strategy=strategy, timeframe=timeframe,
                risk_reward=float(r.risk_reward or 0),
                rsi=float(r.rsi) if r.rsi is not None else None,
                trend=r.trend,
            )
            if was_closed:
                closed += 1
                slots_left += 1  # freed a slot — fall through to open new position

                # Fix 8: Feed the realised P&L from this close back into DailyRiskState
                if today_risk_state is not None and closed_pnl is not None:
                    current_pnl = float(today_risk_state.realised_pnl or 0)
                    today_risk_state.realised_pnl = round(current_pnl + closed_pnl, 2)

            # ── 2. Try to open a new position ─────────────────────────────
            if slots_left <= 0:
                _add_log(db, symbol=r.symbol, signal=signal, action="SKIP",
                         reason=f"Max positions reached ({open_count}/{cfg.max_open_positions})",
                         strategy=strategy, timeframe=timeframe,
                         entry_price=float(r.entry_price or 0),
                         stop_loss=float(r.stop_loss or 0),
                         target_price=float(r.target_price or 0),
                         risk_reward=float(r.risk_reward or 0),
                         rsi=float(r.rsi) if r.rsi is not None else None,
                         trend=r.trend)
                continue

            # Fix Group 1 (V-01 / V-02): Distributed Portfolio Lock
            # ─────────────────────────────────────────────────────
            # Without this lock two concurrent workers (reactive + scheduled)
            # both read the same portfolio state, both see N positions < limit,
            # and both call _open_trade() — resulting in a duplicate position
            # and risk-limit bypass. The lock serialises each trade-open attempt
            # across ALL processes sharing the same Redis instance.
            # TTL=30s: if the lock holder dies, the lock auto-expires in 30s.
            try:
                async with redis_lock("quantdss:portfolio_lock", timeout=30):
                    # Re-check slot count INSIDE the lock with a fresh read
                    open_count_now = await _count_open_trades(db)
                    slots_now = cfg.max_open_positions - open_count_now
                    if slots_now <= 0:
                        logger.info(
                            f"AutoTrader: Portfolio full (locked re-check — "
                            f"{open_count_now}/{cfg.max_open_positions}). Skipping {r.symbol}."
                        )
                        continue

                    # Fix 6: Execution Idempotency
                    from app.core.redis import redis_client
                    import hashlib
                    # Generate a unique deterministic signal ID
                    signal_str = f"{r.symbol}_{signal}_{strategy}_{timeframe}_{float(r.entry_price or 0)}"
                    signal_id = hashlib.md5(signal_str.encode()).hexdigest()
                    dedup_key = f"execution_dedup:{signal_id}"
                    
                    if await redis_client.exists(dedup_key):
                        logger.info(f"AutoTrader: Idempotency execution check skipped duplicate signal {signal_id} for {r.symbol}.")
                        continue
                    
                    ok = await _open_trade(
                        db=db, cfg=cfg,
                        symbol=r.symbol,
                        signal=signal,
                        entry_price=float(r.entry_price or 0),
                        stop_loss=float(r.stop_loss or 0),
                        target_price=float(r.target_price or 0),
                        risk_state=today_risk_state,
                        portfolio=portfolio,
                        instrument_key=getattr(r, "instrument_key", ""),
                        risk_reward=float(r.risk_reward or 0),
                        rsi=float(r.rsi) if r.rsi is not None else None,
                        trend=r.trend,
                        strategy=strategy,
                        timeframe=timeframe,
                        signal_id=signal_id,  # Pass down to execution manager
                    )
                    if ok:
                        # Set deduplication key on success with 1-hour TTL
                        await redis_client.setex(dedup_key, 3600, "1")
                        opened += 1
                        slots_left -= 1
                    await db.commit()
            except TimeoutError:
                logger.warning(
                    f"AutoTrader: Could not acquire portfolio lock for {r.symbol} — skipping signal. "
                    f"(Another worker is placing a trade right now.)"
                )

        # Always commit so SKIP log entries are persisted for the Activity Log
        await db.commit()
        total_signals = len([r for r in results if getattr(r, 'signal', None) in ('BUY', 'SELL')])
        if opened or closed:
            logger.info(f"AutoTrader (reactive): {total_signals} signal(s) → {opened} opened, {closed} closed")
        else:
            logger.info(f"AutoTrader (reactive): {total_signals} signal(s) evaluated, 0 actions (see Activity Log)")


# ──────────────────────────────────────────────────────────────────────────────
# SCHEDULED MODE — runs on its own watchlist via APScheduler
# ──────────────────────────────────────────────────────────────────────────────

async def run_auto_trader() -> None:
    """
    SCHEDULED MODE — called every N minutes by APScheduler.

    Architecture Audit Fix V-01: This function now routes ALL signals through
    the intelligence pipeline (SignalPool → Consolidation → MetaStrategy →
    Confirmation → Quality → Regime → ML → NLP → Time → Liquidity →
    FinalAlert → RiskEngine → AutoTraderQueue) instead of bypassing it.

    The full pipeline ensures:
      - Strategy health and regime checks (MetaStrategy)
      - Multi-strategy confirmation and quality scoring
      - Risk Engine validation (via FinalAlertGenerator)
      - Signal traceability with trace_id
    """
    if not _is_market_hours():
        logger.debug("AutoTrader: outside market hours — skipping")
        return

    async with async_session_factory() as db:
        cfg = await _get_config(db)
        if not cfg or not cfg.enabled:
            logger.debug("AutoTrader: disabled — skipping")
            return

        watchlist: list[str] = list(cfg.watchlist or [])
        if not watchlist:
            logger.debug("AutoTrader: watchlist is empty — skipping")
            return

        # Check halt state
        from app.models.daily_risk_state import DailyRiskState
        today = _date.today()
        risk_state_result = await db.execute(
            select(DailyRiskState).where(DailyRiskState.trade_date == today)
        )
        today_risk_state = risk_state_result.scalar_one_or_none()
        if today_risk_state and today_risk_state.is_halted:
            logger.info("AutoTrader: DailyRiskState.is_halted=True — skipping scan")
            return

        logger.info(
            f"AutoTrader: scanning {len(watchlist)} symbols "
            f"(strategy={cfg.strategy}, tf={cfg.timeframe}) → intelligence pipeline"
        )

    # Scan watchlist and route through intelligence pipeline
    from app.api.routers.scanner import _scan_one
    from app.engine.signal_pool import signal_pool
    from app.engine.base_strategy import CandidateSignal
    from app.engine.signal_dedup import signal_dedup
    from app.engine.signal_trace import SignalTracer

    trace_id = SignalTracer.new_trace_id()
    fed = 0

    for symbol in watchlist:
        try:
            result = await _scan_one(symbol, cfg.strategy, cfg.timeframe)
            if result.signal not in ("BUY", "SELL"):
                continue

            # Convert scanner result → CandidateSignal for pipeline
            candidate = CandidateSignal(
                symbol_id=0,
                strategy_id=0,
                strategy_name=cfg.strategy,
                signal_type=result.signal,
                entry_price=float(result.entry_price or 0),
                stop_loss=float(result.stop_loss or 0),
                target_price=float(result.target_price or 0),
                atr_value=abs(float(result.entry_price or 0) - float(result.stop_loss or 0)),
                candle_time=datetime.now(IST),
                confidence_score=float(result.risk_reward or 0) * 20,
                metadata={
                    "symbol_name": symbol,
                    "source": "scheduled_auto_trader",
                    "trace_id": trace_id,
                },
            )

            # Dedup check (Implicitly records if it wasn't a duplicate)
            if await signal_dedup.is_duplicate(
                candidate.symbol_id, candidate.strategy_id, candidate.candle_time
            ):
                SignalTracer.trace_drop(
                    trace_id, "DEDUP_CHECK", symbol,
                    "Duplicate from scheduled scan suppressed"
                )
                continue

            await signal_pool.add_signal(candidate)
            fed += 1

        except Exception as e:
            logger.error(f"AutoTrader: error scanning {symbol}: {e}")

    if fed:
        SignalTracer.trace_pass(
            trace_id, "SIGNAL_POOL", cfg.strategy,
            f"{fed} scheduled scan signal(s) queued via intelligence pipeline"
        )
        logger.info(
            f"AutoTrader (scheduled): {fed} signal(s) routed to intelligence "
            f"pipeline (trace={trace_id})"
        )


# ──────────────────────────────────────────────────────────────────────────────
# EOF
# ──────────────────────────────────────────────────────────────────────────────
