"""
RiskEngine — Fail-fast ordered rule chain validating signals.
Every signal must pass ALL rules before being approved.

Fixes applied (Audit Phase 2):
  - Rule 0.1: MinRiskRewardFilter — blocks signals below configured R:R minimum
  - Rule 1b: WeeklyLossCircuitBreaker — blocks when weekly loss exceeds cap
  - MaxSignalsPerStockPerDay: now DB-persisted, not in-memory (restart-safe)
  - SECTOR_MAP: expanded to all Nifty 50 + Bank Nifty constituents
  - GlobalMarketRegimeFilter: reads from config (which is auto-updated by scheduler)
  - UniquePositionSizer: single pricing authority replacing _calc_qty dual-system gap

Fix 4 (Liquidity ADV):
  - LiquidityFilter now reads from RiskEngine.adv_cache (loaded at 09:15 from DB).
  - Previous code compared intra-minute volume to ADV threshold, blocking all signals.
  - RiskEngine.load_adv(adv_map) populates the cache at session start.

Fix 5 (Gross Portfolio Exposure):
  - New GrossExposureFilter rule: blocks when sum(open_position_values) >= max_exposure.
  - Portfolio.open_position_values: list[float] carries current open trade notionals.

Fix 6 (Position Sizer Risk Budget):
  - PositionSizer deducts committed_risk from total_risk before sizing.
  - Portfolio.committed_risk: float carries risk already used by open trades.
"""
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime, timezone, timedelta
from app.core.logging import logger
from app.core.notifier import notifier
from app.engine.base_strategy import RawSignal
from app.ingestion.websocket_manager import market_data_cache

# ── Alert deduplication registry ──────────────────────────────
# Maps (alert_key) → last-sent epoch second. Prevents Telegram spam.
_alert_cooldown: dict[str, float] = {}
_ALERT_COOLDOWN_S = 300  # Minimum 5 minutes between same-type alerts

def _send_deduped_alert(title: str, message: str, level: str, key: str) -> None:
    """Send an alert only if the same key hasn't fired within _ALERT_COOLDOWN_S."""
    import time, asyncio
    now = time.monotonic()
    if now - _alert_cooldown.get(key, 0) < _ALERT_COOLDOWN_S:
        return
    _alert_cooldown[key] = now
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(notifier.send_alert(title=title, message=message, level=level))
    except RuntimeError:
        pass  # Synchronous test env, no loop


# ── Global Application State for Circuit Breaker ──────────────
consecutive_api_errors = 0

def increment_api_error():
    global consecutive_api_errors
    consecutive_api_errors += 1

def reset_api_error():
    global consecutive_api_errors
    consecutive_api_errors = 0


@dataclass
class Portfolio:
    """Current trading portfolio state."""
    current_balance: float
    peak_balance: float
    open_positions: int = 0
    open_symbols: list[str] = field(default_factory=list)
    # Weekly P&L tracking (loaded by the auto-trader from DB)
    weekly_realised_pnl: float = 0.0
    # Fix 5: Sum of open trade notional values (entry_price * quantity per trade)
    open_position_values: list[float] = field(default_factory=list)
    # Fix 6: Sum of risk amounts already committed by open positions
    committed_risk: float = 0.0


@dataclass
class RiskDecision:
    """Output of the Risk Engine validation."""
    status: str                     # APPROVED / BLOCKED / SKIPPED
    reason: str | None = None
    quantity: int | None = None
    risk_amount: float | None = None
    risk_pct: float | None = None
    risk_reward: float | None = None


class RiskRule:
    """Base class for all risk rules."""

    def __init__(self, config):
        self.config = config

    def check(self, signal: RawSignal, state, portfolio: Portfolio) -> RiskDecision:
        raise NotImplementedError


# ─── Rule 0: Consecutive Errors Circuit Breaker ─────────────
class ConsecutiveErrorsCircuitBreaker(RiskRule):
    """BLOCK all trading if consecutive broker API errors exceed threshold."""
    def check(self, signal: RawSignal, state, portfolio: Portfolio) -> RiskDecision:
        global consecutive_api_errors
        max_errors = int(getattr(self.config, "max_consecutive_errors", 5))
        if consecutive_api_errors >= max_errors:
            return RiskDecision(
                status="BLOCKED",
                reason=f"CONSECUTIVE_ERRORS_LIMIT ({consecutive_api_errors})",
            )
        return RiskDecision(status="PASS")


# ─── Rule 0.1: Minimum Risk:Reward Filter ────────────────────
class MinRiskRewardFilter(RiskRule):
    """
    SKIP signals where the R:R is below the configured minimum.
    Fix: R:R was stored but never enforced. This is now a hard gate.
    """
    def check(self, signal: RawSignal, state, portfolio: Portfolio) -> RiskDecision:
        min_rr = float(getattr(self.config, "min_risk_reward", 1.5))
        rr = signal.risk_reward
        if rr is None or rr < min_rr:
            return RiskDecision(
                status="SKIPPED",
                reason=f"LOW_RISK_REWARD (R:R={rr:.2f} < min={min_rr:.2f})" if rr else "NO_RISK_REWARD",
            )
        return RiskDecision(status="PASS")


# ─── Rule 0.5: Global Market Regime Filter ──────────────────
class GlobalMarketRegimeFilter(RiskRule):
    """BLOCK/SKIP signals if the master market regime invalidates the strategy."""
    def check(self, signal: RawSignal, state, portfolio: Portfolio) -> RiskDecision:
        regime = getattr(self.config, "market_regime", "NONE")
        strategy = getattr(signal, "strategy", "").upper()

        # In a TRENDING market, skip Mean-Reversion strategies.
        if regime == "TRENDING" and ("REVERSION" in strategy or "RSI" in strategy):
            return RiskDecision(status="SKIPPED", reason="REGIME_MISMATCH_TRENDING")

        # In a RANGING market, skip Trend-Following strategies.
        if regime == "RANGING" and ("TREND" in strategy or "BREAKOUT" in strategy or "EMA" in strategy):
            return RiskDecision(status="SKIPPED", reason="REGIME_MISMATCH_RANGING")

        # In HIGH_VOLATILITY: skip ORB, Volume Expansion and Trend strategies
        if regime == "HIGH_VOLATILITY" and (
            "ORB" in strategy or "VOLUME" in strategy or "TREND" in strategy
        ):
            return RiskDecision(status="SKIPPED", reason="REGIME_MISMATCH_HIGH_VOL")

        return RiskDecision(status="PASS")


# ─── Rule 1: Daily Loss Circuit Breaker ──────────────────────
class DailyLossCircuitBreaker(RiskRule):
    """BLOCK if today's realised P&L exceeds the daily loss cap."""

    def check(self, signal: RawSignal, state, portfolio: Portfolio) -> RiskDecision:
        realised_pnl = float(state.realised_pnl or 0)

        loss_cap_inr = float(self.config.max_daily_loss_inr)
        loss_cap_pct = portfolio.current_balance * float(self.config.max_daily_loss_pct)
        effective_cap = min(loss_cap_inr, loss_cap_pct)

        if realised_pnl <= -effective_cap:
            msg = f"**Daily Loss Limit Hit**\nRealised: ₹{realised_pnl:.2f}\nCap: ₹{effective_cap:.2f}"
            _send_deduped_alert("Circuit Breaker Active", msg, "CRITICAL", "daily_loss")
            return RiskDecision(status="BLOCKED", reason="DAILY_LOSS_LIMIT_REACHED")
        return RiskDecision(status="PASS")


# ─── Rule 1b: Weekly Loss Circuit Breaker (NEW) ───────────────
class WeeklyLossCircuitBreaker(RiskRule):
    """
    BLOCK if this week's cumulative realised P&L exceeds the weekly loss cap.
    Fix: The daily cap alone allows daily limit × 5 losses in a week.
    """
    def check(self, signal: RawSignal, state, portfolio: Portfolio) -> RiskDecision:
        weekly_pnl = portfolio.weekly_realised_pnl

        loss_cap_inr = float(getattr(self.config, "max_weekly_loss_inr", 2000.0))
        loss_cap_pct = portfolio.current_balance * float(getattr(self.config, "max_weekly_loss_pct", 0.05))
        effective_cap = min(loss_cap_inr, loss_cap_pct)

        if weekly_pnl <= -effective_cap:
            msg = f"**Weekly Loss Limit Hit**\nWeekly P&L: ₹{weekly_pnl:.2f}\nCap: ₹{effective_cap:.2f}"
            _send_deduped_alert("Weekly Circuit Breaker", msg, "CRITICAL", "weekly_loss")
            return RiskDecision(status="BLOCKED", reason="WEEKLY_LOSS_LIMIT_REACHED")
        return RiskDecision(status="PASS")


# ─── Rule 2: Account Drawdown Halt ──────────────────────────
class AccountDrawdownHalt(RiskRule):
    """BLOCK if account has drawn down ≥ max_drawdown_pct from peak."""

    def check(self, signal: RawSignal, state, portfolio: Portfolio) -> RiskDecision:
        if portfolio.peak_balance <= 0:
            return RiskDecision(status="PASS")

        drawdown_pct = (portfolio.peak_balance - portfolio.current_balance) / portfolio.peak_balance

        if drawdown_pct >= float(self.config.max_account_drawdown_pct):
            msg = (
                f"**Drawdown Halt**\n"
                f"Peak: ₹{portfolio.peak_balance:.2f}\n"
                f"Current: ₹{portfolio.current_balance:.2f}\n"
                f"DD: {drawdown_pct*100:.2f}%"
            )
            _send_deduped_alert("Account Drawdown Halt", msg, "CRITICAL", "drawdown")
            return RiskDecision(status="BLOCKED", reason="ACCOUNT_DRAWDOWN_HALT")
        return RiskDecision(status="PASS")


# ─── Rule 3: Cooldown Filter ────────────────────────────────
class CooldownFilter(RiskRule):
    """SKIP if a signal was approved within the cooldown window."""

    def check(self, signal: RawSignal, state, portfolio: Portfolio) -> RiskDecision:
        last_signal_time = state.last_signal_time
        if last_signal_time is None:
            return RiskDecision(status="PASS")

        now = datetime.now(UTC)
        if last_signal_time.tzinfo is None:
            last_signal_time = last_signal_time.replace(tzinfo=UTC)

        elapsed_minutes = (now - last_signal_time).total_seconds() / 60
        cooldown = int(self.config.cooldown_minutes)

        if elapsed_minutes < cooldown:
            return RiskDecision(status="SKIPPED", reason="COOLDOWN_ACTIVE")
        return RiskDecision(status="PASS")


# ─── Rule 4: Volatility Filter ──────────────────────────────
class VolatilityFilter(RiskRule):
    """SKIP if ATR% is outside the configured volatility band."""

    def check(self, signal: RawSignal, state, portfolio: Portfolio) -> RiskDecision:
        if signal.entry_price <= 0:
            return RiskDecision(status="SKIPPED", reason="INVALID_ENTRY_PRICE")

        atr_pct = (signal.atr_value / signal.entry_price) * 100
        min_atr = float(self.config.min_atr_pct) * 100
        max_atr = float(self.config.max_atr_pct) * 100

        if atr_pct < min_atr:
            return RiskDecision(status="SKIPPED", reason="LOW_VOLATILITY")
        if atr_pct > max_atr:
            return RiskDecision(status="SKIPPED", reason="HIGH_VOLATILITY")

        return RiskDecision(status="PASS")


# ─── Rule 5: Position Sizer ────────────────────────────────────
class PositionSizer(RiskRule):
    """
    Single authoritative position sizing rule.
    Fix: The auto_trader_engine._calc_qty() shadow calculation has been retired.
    All sizing now runs through here.

    Fix 6: Deducts committed_risk from total_risk_cap before sizing,
    so the total risk across all open + new positions never exceeds the cap.
    """

    def check(self, signal: RawSignal, state, portfolio: Portfolio) -> RiskDecision:
        stop_distance = abs(signal.entry_price - signal.stop_loss)

        if stop_distance == 0:
            return RiskDecision(status="BLOCKED", reason="INVALID_STOP_LOSS")

        risk_per_trade = float(self.config.risk_per_trade_pct)
        total_risk_cap = portfolio.current_balance * risk_per_trade

        # Fix 6: Subtract risk already committed by open positions
        available_risk = max(0.0, total_risk_cap - portfolio.committed_risk)

        if available_risk <= 0:
            return RiskDecision(
                status="BLOCKED",
                reason=f"NO_RISK_BUDGET_LEFT (committed=₹{portfolio.committed_risk:.0f}, "
                       f"cap=₹{total_risk_cap:.0f})",
            )

        quantity = math.floor(available_risk / stop_distance)

        if quantity < 1:
            return RiskDecision(status="BLOCKED", reason="INSUFFICIENT_CAPITAL")

        risk_pct = round(available_risk / portfolio.current_balance * 100, 4)

        return RiskDecision(
            status="PASS",
            quantity=quantity,
            risk_amount=round(available_risk, 2),
            risk_pct=risk_pct,
        )


# ─── Rule 6: Max Position Size Cap ──────────────────────────
class MaxPositionSizeCap(RiskRule):
    """Adjustment — caps quantity if position value exceeds max_position_pct."""

    def check(self, signal: RawSignal, state, portfolio: Portfolio) -> RiskDecision:
        return RiskDecision(status="PASS")

    def adjust_quantity(self, quantity: int, signal: RawSignal, portfolio: Portfolio) -> int:
        """Cap position value at max_position_pct of balance."""
        position_value = quantity * signal.entry_price
        max_value = portfolio.current_balance * float(getattr(self.config, "max_position_pct", 0.20))

        if position_value > max_value:
            capped_qty = math.floor(max_value / signal.entry_price)
            logger.info(f"Position cap: {quantity} → {capped_qty} (max {float(getattr(self.config, 'max_position_pct', 0.20))*100}%)")
            return max(capped_qty, 1)
        return quantity


# ─── Rule 7: Max Concurrent Positions ────────────────────────
class MaxConcurrentPositions(RiskRule):
    """SKIP if open position count is at the maximum."""

    def check(self, signal: RawSignal, state, portfolio: Portfolio) -> RiskDecision:
        max_positions = int(self.config.max_concurrent_positions)

        if portfolio.open_positions >= max_positions:
            return RiskDecision(status="SKIPPED", reason="MAX_POSITIONS_OPEN")
        return RiskDecision(status="PASS")


# ─── Rule 7b: Gross Portfolio Exposure (NEW) ────────────────────────────
class GrossExposureFilter(RiskRule):
    """
    Fix 5: SKIP if total open position notional already exceeds the portfolio cap.

    max_portfolio_exposure_pct (default 40%) is the maximum fraction of the
    account balance that can be deployed in open positions at any moment.

    Uses Portfolio.open_position_values populated by auto_trader_engine before
    calling risk engine. Each element is entry_price * quantity for one trade.
    """
    def check(self, signal: RawSignal, state, portfolio: Portfolio) -> RiskDecision:
        max_exp_pct = float(getattr(self.config, "max_portfolio_exposure_pct", 0.40))
        max_exposure = portfolio.current_balance * max_exp_pct
        current_notional = sum(portfolio.open_position_values)

        if current_notional >= max_exposure:
            return RiskDecision(
                status="SKIPPED",
                reason=(
                    f"GROSS_EXPOSURE_LIMIT "
                    f"(₹{current_notional:,.0f} ≥ cap ₹{max_exposure:,.0f}, "
                    f"{max_exp_pct*100:.0f}% of ₹{portfolio.current_balance:,.0f})"
                ),
            )
        return RiskDecision(status="PASS")


# ─── Rule 8: Liquidity Filter ────────────────────────────────────
class LiquidityFilter(RiskRule):
    """
    SKIP if symbol's ADV (Average Daily Volume) is below configured threshold.

    Fix 4: CRITICAL CORRECTION
    The previous version compared intra-minute volume (which could be 0–500 shares)
    against ADV thresholds of 500,000+, blocking virtually every signal.

    New approach:
      - At session start (09:15 IST), call RiskEngine.load_adv(adv_map) to cache
        the 5-day or 10-day average daily volume per symbol from the database.
      - LiquidityFilter.check() reads from this cache — not live volume.
      - If a symbol is not in the ADV cache, the filter PASSES (fail-open for
        symbols without historical data rather than blocking everything).
    """

    # Injected by RiskEngine at construction time (shared reference)
    adv_cache: dict = {}

    def check(self, signal: RawSignal, state, portfolio: Portfolio) -> RiskDecision:
        min_adv = int(getattr(self.config, "min_daily_volume", 500_000))

        symbol_key = str(signal.symbol_id)
        adv = self.adv_cache.get(symbol_key, -1)

        if adv == -1:
            # Fix 11: Fail-closed if ADV data is missing
            return RiskDecision(
                status="BLOCKED",
                reason=f"ADV_DATA_MISSING",
            )

        if adv < min_adv:
            return RiskDecision(
                status="SKIPPED",
                reason=f"LOW_ADV (ADV={adv:,} < min={min_adv:,})",
            )
        return RiskDecision(status="PASS")


# ─── Rule 9: Spread Filter ────────────────────────────────────
class SpreadFilter(RiskRule):
    """
    SKIP if current bid-ask spread is wider than max_spread_pct.
    Fix: bid/ask are now populated by the WebSocket manager, so this filter
    is functional. If data is missing (e.g., outside market hours) it passes.
    """
    def check(self, signal: RawSignal, state, portfolio: Portfolio) -> RiskDecision:
        max_spread = float(getattr(self.config, "max_spread_pct", 0.005))

        instrument_key = getattr(signal, "instrument_key", None)
        if not instrument_key:
            return RiskDecision(status="PASS")

        best_bid, best_ask = market_data_cache.get_quote(instrument_key)

        if best_bid is None or best_ask is None or best_bid <= 0:
            return RiskDecision(status="PASS")   # No data — allow

        spread = (best_ask - best_bid) / best_bid
        if spread > max_spread:
            return RiskDecision(
                status="SKIPPED",
                reason=f"SPREAD_TOO_WIDE ({spread*100:.2f}% > {max_spread*100:.2f}%)",
            )
        return RiskDecision(status="PASS")


# ─── Rule 10: Signal Time Gate ─────────────────────────────────
IST = timezone(timedelta(hours=5, minutes=30))

class SignalTimeGateFilter(RiskRule):
    """
    SKIP signals that arrive outside the allowed trading window.
    Window is now DB-configurable (signal_start_hour/minute, signal_end_hour/minute).
    """

    def check(self, signal: RawSignal, state, portfolio: Portfolio) -> RiskDecision:
        start_h = int(getattr(self.config, "signal_start_hour",   9))
        start_m = int(getattr(self.config, "signal_start_minute", 20))
        end_h   = int(getattr(self.config, "signal_end_hour",    14))
        end_m   = int(getattr(self.config, "signal_end_minute",  30))

        # Fix 10: Use current execution time, not candle time
        now_ist = datetime.now(IST)
        now_min = now_ist.hour * 60 + now_ist.minute
        start_min = start_h * 60 + start_m
        end_min   = end_h   * 60 + end_m

        if not (start_min <= now_min <= end_min):
            return RiskDecision(
                status="SKIPPED",
                reason=(
                    f"OUTSIDE_SIGNAL_WINDOW "
                    f"({now_ist.strftime('%H:%M')} IST, "
                    f"window {start_h:02d}:{start_m:02d}-{end_h:02d}:{end_m:02d})"
                ),
            )
        return RiskDecision(status="PASS")


# ─── Rule 11: Max Signals Per Stock Per Day ────────────────────────────────────
class MaxSignalsPerStockPerDay(RiskRule):
    """
    SKIP if this stock has already hit the daily signal cap.

    Fix (Issue 7): Count is now persisted to the DailyRiskState table via the
    signals_per_stock JSON column, so it survives server restarts.

    Lifecycle:
      - Startup: hydrate_signal_counts(state) loads today's counts from DB.
      - Runtime: check() reads the fast in-memory cache first, then writes
        back to state.signals_per_stock on every approved signal so the ORM
        flush in _update_risk_state() persists the change atomically.
    """

    def __init__(self, config):
        super().__init__(config)
        # symbol_id (str) → (date_str, count)  — fast in-memory path
        self._counts: dict[str, tuple[str, int]] = {}

    def hydrate_signal_counts(self, state) -> None:
        """
        Issue 7 Fix: Load today's per-stock signal counts from the DB state row
        into the in-memory dict.  Call once per candle batch before the rule chain.
        """
        today = datetime.now(IST).strftime("%Y-%m-%d")
        db_counts: dict = state.signals_per_stock or {}
        for sym_id_str, count in db_counts.items():
            existing = self._counts.get(sym_id_str)
            # Only load if we don't already have a fresher in-memory entry
            if existing is None or existing[0] != today:
                self._counts[sym_id_str] = (today, int(count))

    def _write_back(self, state, sid: str, count: int) -> None:
        """Persist the updated count to the ORM row's JSON column."""
        try:
            current = dict(state.signals_per_stock or {})
            current[sid] = count
            state.signals_per_stock = current
        except Exception:
            pass  # non-fatal — in-memory still prevents immediate dupes

    def check(self, signal: RawSignal, state, portfolio: Portfolio) -> RiskDecision:
        max_per = int(getattr(self.config, "max_signals_per_stock", 3))
        today   = datetime.now(IST).strftime("%Y-%m-%d")
        sid     = str(signal.symbol_id)   # str key for JSON column compatibility

        record = self._counts.get(sid)
        if record is None or record[0] != today:
            self._counts[sid] = (today, 1)
            self._write_back(state, sid, 1)
            return RiskDecision(status="PASS")

        _date, count = record
        if count >= max_per:
            return RiskDecision(
                status="SKIPPED",
                reason=f"MAX_SIGNALS_PER_STOCK ({count}/{max_per} today)",
            )

        new_count = count + 1
        self._counts[sid] = (today, new_count)
        self._write_back(state, sid, new_count)
        return RiskDecision(status="PASS")


# ─── Rule 12: Correlation / Sector Filter ─────────────────────
# Expanded SECTOR_MAP: Nifty 50 + Bank Nifty constituents (2025)
SECTOR_MAP: dict[str, str] = {
    # Banking
    "HDFCBANK": "BANKING", "ICICIBANK": "BANKING", "SBIN": "BANKING",
    "AXISBANK": "BANKING", "KOTAKBANK": "BANKING", "INDUSINDBK": "BANKING",
    "BANDHANBNK": "BANKING", "FEDERALBNK": "BANKING", "IDFCFIRSTB": "BANKING",
    "AUBANK": "BANKING", "RBLBANK": "BANKING", "BANKBARODA": "BANKING",
    "PNB": "BANKING", "CANARABANK": "BANKING",
    # IT/Technology
    "TCS": "IT", "INFY": "IT", "WIPRO": "IT", "HCLTECH": "IT",
    "TECHM": "IT", "LTI": "IT", "LTIM": "IT", "MPHASIS": "IT",
    "PERSISTENT": "IT", "COFORGE": "IT", "OFSS": "IT",
    # Energy / O&G
    "RELIANCE": "ENERGY", "ONGC": "ENERGY", "BPCL": "ENERGY",
    "IOC": "ENERGY", "GAIL": "ENERGY", "PETRONET": "ENERGY",
    "HINDPETRO": "ENERGY", "ADANIGREEN": "ENERGY", "TATAPOWER": "ENERGY",
    "POWERGRID": "ENERGY", "NTPC": "ENERGY", "ADANIPORTS": "ENERGY",
    # Auto
    "TATAMOTORS": "AUTO", "M&M": "AUTO", "MARUTI": "AUTO",
    "BAJAJ-AUTO": "AUTO", "EICHERMOT": "AUTO", "HEROMOTOCO": "AUTO",
    "TVSMOTOR": "AUTO", "ASHOKLEY": "AUTO", "MOTHERSON": "AUTO",
    # FMCG
    "HINDUNILVR": "FMCG", "ITC": "FMCG", "NESTLEIND": "FMCG",
    "BRITANNIA": "FMCG", "DABUR": "FMCG", "GODREJCP": "FMCG",
    "MARICO": "FMCG", "COLPAL": "FMCG",
    # Pharma
    "SUNPHARMA": "PHARMA", "DRREDDY": "PHARMA", "CIPLA": "PHARMA",
    "DIVISLAB": "PHARMA", "AUROPHARMA": "PHARMA", "LUPIN": "PHARMA",
    "BIOCON": "PHARMA", "TORNTPHARM": "PHARMA",
    # Metals / Materials
    "TATASTEEL": "METALS", "JSWSTEEL": "METALS", "HINDALCO": "METALS",
    "VEDL": "METALS", "COALINDIA": "METALS", "NMDC": "METALS",
    "SAIL": "METALS", "HINDCOPPER": "METALS",
    # Financial Services (Non-banking)
    "BAJFINANCE": "FINANCIALS", "BAJAJFINSV": "FINANCIALS",
    "SBILIFE": "FINANCIALS", "HDFCLIFE": "FINANCIALS",
    "ICICIGI": "FINANCIALS", "CHOLAFIN": "FINANCIALS",
    "MUTHOOTFIN": "FINANCIALS",
    # Consumer Goods / Durables
    "TITAN": "CONSUMER", "ASIANPAINT": "CONSUMER",
    "PIDILITIND": "CONSUMER", "HAVELLS": "CONSUMER",
    "VOLTAS": "CONSUMER",
    # Infra / Cement
    "ULTRACEMCO": "CEMENT", "SHREECEM": "CEMENT", "AMBUJACEMENT": "CEMENT",
    "ACC": "CEMENT", "GRASIM": "CEMENT",
    # Telecom
    "BHARTIARTL": "TELECOM", "IDEA": "TELECOM",
}

class CorrelationFilter(RiskRule):
    """
    SKIP if too many open positions exist in the same asset class/sector.
    Fix: SECTOR_MAP now covers all major Nifty 50 + Bank Nifty symbols.
    """
    def check(self, signal: RawSignal, state, portfolio: Portfolio) -> RiskDecision:
        max_correlated = int(getattr(self.config, "max_correlated_positions", 3))

        # symbol_id might be int or string; prefer symbol_name if available
        raw_sym = str(getattr(signal, "symbol_name", signal.symbol_id))
        signal_base_symbol = raw_sym.split("-")[0].replace(".NS", "")
        sector = SECTOR_MAP.get(signal_base_symbol, "UNKNOWN")

        if sector == "UNKNOWN":
            return RiskDecision(status="PASS")

        sector_count = 0
        for sym in portfolio.open_symbols:
            open_base = sym.split("-")[0].replace(".NS", "")
            if SECTOR_MAP.get(open_base) == sector:
                sector_count += 1

        if sector_count >= max_correlated:
            return RiskDecision(
                status="SKIPPED",
                reason=f"SECTOR_CORRELATION_LIMIT_MET ({sector}: {sector_count}/{max_correlated})",
            )

        return RiskDecision(status="PASS")


# ─── Main Risk Engine ────────────────────────────────────────

class RiskEngine:
    """
    Fail-fast ordered rule chain.

    Rules execute in sequence. The first failure returns immediately.
    All decisions are logged regardless of outcome.

    Rule execution order:
       0.  ConsecutiveErrorsCircuitBreaker — hard halt
       0.1 MinRiskRewardFilter            — soft skip (NEW)
       0.5 GlobalMarketRegimeFilter       — soft skip
       1.  DailyLossCircuitBreaker        — hard halt
       1b. WeeklyLossCircuitBreaker       — hard halt (NEW)
       2.  AccountDrawdownHalt            — hard halt
       3.  CooldownFilter                 — soft skip
       4.  VolatilityFilter               — soft skip
       5.  PositionSizer                  — computes qty, blocks if < 1 (Fix 6: risk budget)
       6.  MaxPositionSizeCap             — adjusts qty downward
       7.  MaxConcurrentPositions         — soft skip
       7b. GrossExposureFilter            — soft skip (Fix 5: NEW)
       8.  LiquidityFilter                — soft skip (Fix 4: now uses ADV cache)
       9.  SpreadFilter                   — soft skip (NOW FUNCTIONAL)
       10. SignalTimeGateFilter           — soft skip
       11. MaxSignalsPerStockPerDay       — soft skip (restart-safe)
       12. CorrelationFilter              — soft skip (expanded sector map)
    """

    def __init__(self, config):
        self.config = config
        self._per_stock_rule = MaxSignalsPerStockPerDay(config)   # stateful — single instance

        # Fix 4: ADV cache loaded at session start via load_adv()
        self.adv_cache: dict[str, int] = {}

        # Fix 4: Inject the shared adv_cache reference into LiquidityFilter
        self._liquidity_filter = LiquidityFilter(config)
        self._liquidity_filter.adv_cache = self.adv_cache

        self.rules = [
            ConsecutiveErrorsCircuitBreaker(config),    # 0
            MinRiskRewardFilter(config),                # 0.1 NEW
            GlobalMarketRegimeFilter(config),           # 0.5
            DailyLossCircuitBreaker(config),            # 1
            WeeklyLossCircuitBreaker(config),           # 1b NEW
            AccountDrawdownHalt(config),                # 2
            CooldownFilter(config),                     # 3
            VolatilityFilter(config),                   # 4
            PositionSizer(config),                      # 5
            MaxPositionSizeCap(config),                 # 6
            MaxConcurrentPositions(config),             # 7
            GrossExposureFilter(config),                # 7b NEW (Fix 5)
            self._liquidity_filter,                     # 8 (Fix 4: ADV cache)
            SpreadFilter(config),                       # 9
            SignalTimeGateFilter(config),               # 10
            self._per_stock_rule,                       # 11
            CorrelationFilter(config),                  # 12
        ]

    def load_adv(self, adv_map: dict) -> None:
        """
        Fix 4: Load Average Daily Volume data into the in-memory cache.

        Call this once at session start (09:15 IST) with a dict mapping
        symbol_id (int or str) → average daily volume (int, 5-day or 10-day average).

        The LiquidityFilter rule shares the same dict reference, so updates
        here are immediately visible to the filter without re-instantiation.
        """
        self.adv_cache.clear()
        # Cast all keys to strings to ensure LiquidityFilter.check() lookups match
        for key, value in adv_map.items():
            self.adv_cache[str(key)] = value
        logger.info(f"RiskEngine: ADV cache loaded for {len(adv_map)} symbols")

    def validate(
        self,
        signal: RawSignal,
        state,
        portfolio: Portfolio,
    ) -> RiskDecision:
        """
        Run the signal through all risk rules in order.

        Returns:
            RiskDecision with APPROVED, BLOCKED, or SKIPPED status
        """
        quantity = None
        risk_amount = None
        risk_pct = None

        for rule in self.rules:
            decision = rule.check(signal, state, portfolio)

            if decision.status == "BLOCKED":
                logger.warning(
                    f"Risk BLOCKED: {decision.reason} "
                    f"(signal={signal.signal_type} {signal.symbol_id})"
                )
                return decision

            if decision.status == "SKIPPED":
                logger.info(
                    f"Risk SKIPPED: {decision.reason} "
                    f"(signal={signal.signal_type} {signal.symbol_id})"
                )
                return decision

            # Carry forward computed values from PositionSizer
            if decision.quantity is not None:
                quantity = decision.quantity
                risk_amount = decision.risk_amount
                risk_pct = decision.risk_pct

        # Apply position size cap after sizing
        if quantity is not None:
            for rule in self.rules:
                if isinstance(rule, MaxPositionSizeCap):
                    quantity = rule.adjust_quantity(quantity, signal, portfolio)
                    break

        # All rules passed — APPROVED
        risk_reward = signal.risk_reward

        logger.info(
            f"Risk APPROVED: {signal.signal_type} {signal.symbol_id} "
            f"qty={quantity} risk=₹{risk_amount} R:R={risk_reward}"
        )

        return RiskDecision(
            status="APPROVED",
            reason=None,
            quantity=quantity,
            risk_amount=risk_amount,
            risk_pct=risk_pct,
            risk_reward=risk_reward,
        )
