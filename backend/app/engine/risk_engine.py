"""
RiskEngine — Fail-fast ordered rule chain validating signals.
Every signal must pass ALL 7 rules before being approved.
"""
import math
from dataclasses import dataclass
from datetime import UTC, datetime

from app.core.logging import logger
from app.engine.base_strategy import RawSignal


@dataclass
class Portfolio:
    """Current trading portfolio state."""
    current_balance: float
    peak_balance: float
    open_positions: int = 0


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


# ─── Rule 1: Daily Loss Circuit Breaker ──────────────────────
class DailyLossCircuitBreaker(RiskRule):
    """BLOCK if today's realised P&L exceeds the daily loss cap."""

    def check(self, signal: RawSignal, state, portfolio: Portfolio) -> RiskDecision:
        realised_pnl = float(state.realised_pnl or 0)

        # max_daily_loss is the lower of: configured ₹ cap OR (balance × daily_loss_pct)
        loss_cap_inr = float(self.config.max_daily_loss_inr)
        loss_cap_pct = portfolio.current_balance * float(self.config.max_daily_loss_pct)
        effective_cap = min(loss_cap_inr, loss_cap_pct)

        if realised_pnl <= -effective_cap:
            return RiskDecision(
                status="BLOCKED",
                reason="DAILY_LOSS_LIMIT_REACHED",
            )
        return RiskDecision(status="PASS")


# ─── Rule 2: Account Drawdown Halt ──────────────────────────
class AccountDrawdownHalt(RiskRule):
    """BLOCK if account has drawn down ≥ max_drawdown_pct from peak."""

    def check(self, signal: RawSignal, state, portfolio: Portfolio) -> RiskDecision:
        if portfolio.peak_balance <= 0:
            return RiskDecision(status="PASS")

        drawdown_pct = (portfolio.peak_balance - portfolio.current_balance) / portfolio.peak_balance

        if drawdown_pct >= float(self.config.max_account_drawdown_pct):
            return RiskDecision(
                status="BLOCKED",
                reason="ACCOUNT_DRAWDOWN_HALT",
            )
        return RiskDecision(status="PASS")


# ─── Rule 3: Cooldown Filter ────────────────────────────────
class CooldownFilter(RiskRule):
    """SKIP if a signal was approved within the cooldown window."""

    def check(self, signal: RawSignal, state, portfolio: Portfolio) -> RiskDecision:
        last_signal_time = state.last_signal_time
        if last_signal_time is None:
            return RiskDecision(status="PASS")

        now = datetime.now(UTC)

        # Handle timezone-naive datetimes
        if last_signal_time.tzinfo is None:
            last_signal_time = last_signal_time.replace(tzinfo=UTC)

        elapsed_minutes = (now - last_signal_time).total_seconds() / 60
        cooldown = int(self.config.cooldown_minutes)

        if elapsed_minutes < cooldown:
            return RiskDecision(
                status="SKIPPED",
                reason="COOLDOWN_ACTIVE",
            )
        return RiskDecision(status="PASS")


# ─── Rule 4: Volatility Filter ──────────────────────────────
class VolatilityFilter(RiskRule):
    """SKIP if ATR% is outside the configured volatility band."""

    def check(self, signal: RawSignal, state, portfolio: Portfolio) -> RiskDecision:
        if signal.entry_price <= 0:
            return RiskDecision(status="SKIPPED", reason="INVALID_ENTRY_PRICE")

        atr_pct = (signal.atr_value / signal.entry_price) * 100
        min_atr = float(self.config.min_atr_pct) * 100  # Convert to %
        max_atr = float(self.config.max_atr_pct) * 100

        if atr_pct < min_atr:
            return RiskDecision(status="SKIPPED", reason="LOW_VOLATILITY")
        if atr_pct > max_atr:
            return RiskDecision(status="SKIPPED", reason="HIGH_VOLATILITY")

        return RiskDecision(status="PASS")


# ─── Rule 5: Position Sizer ─────────────────────────────────
class PositionSizer(RiskRule):
    """BLOCK if calculated quantity < 1. Otherwise, computes position size."""

    def check(self, signal: RawSignal, state, portfolio: Portfolio) -> RiskDecision:
        stop_distance = abs(signal.entry_price - signal.stop_loss)

        if stop_distance == 0:
            return RiskDecision(status="BLOCKED", reason="INVALID_STOP_LOSS")

        risk_per_trade = float(self.config.risk_per_trade_pct)
        risk_amount = portfolio.current_balance * risk_per_trade
        quantity = math.floor(risk_amount / stop_distance)

        if quantity < 1:
            return RiskDecision(status="BLOCKED", reason="INSUFFICIENT_CAPITAL")

        risk_pct = round(risk_amount / portfolio.current_balance * 100, 4)

        return RiskDecision(
            status="PASS",
            quantity=quantity,
            risk_amount=round(risk_amount, 2),
            risk_pct=risk_pct,
        )


# ─── Rule 6: Max Position Size Cap ──────────────────────────
class MaxPositionSizeCap(RiskRule):
    """Adjustment — caps quantity if position value exceeds max_position_pct."""

    def check(self, signal: RawSignal, state, portfolio: Portfolio) -> RiskDecision:
        # This rule expects quantity to already be set by PositionSizer
        # It will be called with a 'carry' quantity from the previous rule
        return RiskDecision(status="PASS")

    def adjust_quantity(self, quantity: int, signal: RawSignal, portfolio: Portfolio) -> int:
        """Cap position value at max_position_pct of balance."""
        position_value = quantity * signal.entry_price
        max_value = portfolio.current_balance * float(self.config.max_position_pct)

        if position_value > max_value:
            capped_qty = math.floor(max_value / signal.entry_price)
            logger.info(f"Position cap: {quantity} → {capped_qty} (max {float(self.config.max_position_pct)*100}%)")
            return max(capped_qty, 1)
        return quantity


# ─── Rule 7: Max Concurrent Positions ────────────────────────
class MaxConcurrentPositions(RiskRule):
    """SKIP if open position count is at the maximum."""

    def check(self, signal: RawSignal, state, portfolio: Portfolio) -> RiskDecision:
        max_positions = int(self.config.max_concurrent_positions)

        if portfolio.open_positions >= max_positions:
            return RiskDecision(
                status="SKIPPED",
                reason="MAX_POSITIONS_OPEN",
            )
        return RiskDecision(status="PASS")


# ─── Main Risk Engine ────────────────────────────────────────

class RiskEngine:
    """
    Fail-fast ordered rule chain.

    Rules execute in sequence. The first failure returns immediately.
    All decisions are logged regardless of outcome.

    Rule execution order:
      1. DailyLossCircuitBreaker — hard halt
      2. AccountDrawdownHalt — hard halt (manual reset)
      3. CooldownFilter — soft skip
      4. VolatilityFilter — soft skip
      5. PositionSizer — computes qty, blocks if < 1
      6. MaxPositionSizeCap — adjusts qty downward
      7. MaxConcurrentPositions — soft skip
    """

    def __init__(self, config):
        self.config = config
        self.rules = [
            DailyLossCircuitBreaker(config),
            AccountDrawdownHalt(config),
            CooldownFilter(config),
            VolatilityFilter(config),
            PositionSizer(config),
            MaxPositionSizeCap(config),
            MaxConcurrentPositions(config),
        ]

    def validate(
        self,
        signal: RawSignal,
        state,
        portfolio: Portfolio,
    ) -> RiskDecision:
        """
        Run the signal through all 7 risk rules in order.

        Args:
            signal: Raw signal from the strategy engine
            state: DailyRiskState (from DB or simulated)
            portfolio: Current portfolio state

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

            # Carry forward computed values
            if decision.quantity is not None:
                quantity = decision.quantity
                risk_amount = decision.risk_amount
                risk_pct = decision.risk_pct

        # Apply position size cap (Rule 6 adjustment)
        if quantity is not None:
            cap_rule = self.rules[5]  # MaxPositionSizeCap
            if hasattr(cap_rule, "adjust_quantity"):
                quantity = cap_rule.adjust_quantity(quantity, signal, portfolio)

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
