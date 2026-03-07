"""
Unit tests for the Risk Engine — All 7 rules.
Target: 100% branch coverage for the Risk Engine.
"""
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.engine.base_strategy import RawSignal
from app.engine.risk_engine import (
    AccountDrawdownHalt,
    CooldownFilter,
    DailyLossCircuitBreaker,
    MaxConcurrentPositions,
    MaxPositionSizeCap,
    MaxSignalsPerStockPerDay,
    Portfolio,
    PositionSizer,
    RiskEngine,
    SignalTimeGateFilter,
    VolatilityFilter,
)

# ─── Test Fixtures ────────────────────────────────────────────

@dataclass
class FakeRiskConfig:
    """Fake risk config matching RiskConfig model interface."""
    risk_per_trade_pct: float = 0.01
    max_daily_loss_inr: float = 500.0
    max_daily_loss_pct: float = 0.02
    max_account_drawdown_pct: float = 0.10
    cooldown_minutes: int = 15
    min_atr_pct: float = 0.003
    max_atr_pct: float = 0.030
    max_position_pct: float = 0.20
    max_concurrent_positions: int = 2
    # Time-gate: open all day so tests pass regardless of when CI runs
    signal_start_hour: int = 0
    signal_start_minute: int = 0
    signal_end_hour: int = 23
    signal_end_minute: int = 59
    # Per-stock daily signal limit: very high so it never interferes with unit checks
    max_signals_per_stock: int = 999


@dataclass
class FakeDailyRiskState:
    """Fake daily risk state for testing."""
    realised_pnl: float = 0.0
    last_signal_time: datetime | None = None
    is_halted: bool = False


def make_signal(
    entry_price: float = 100.0,
    stop_loss: float = 98.0,
    target_price: float = 104.0,
    atr_value: float = 2.0,
    signal_type: str = "BUY",
) -> RawSignal:
    """Create a test RawSignal."""
    return RawSignal(
        symbol_id=1,
        strategy_id=1,
        strategy_name="TestStrategy",
        signal_type=signal_type,
        entry_price=entry_price,
        stop_loss=stop_loss,
        target_price=target_price,
        atr_value=atr_value,
        candle_time=datetime.now(UTC),
    )


# ─── Rule 1: Daily Loss Circuit Breaker ──────────────────────

class TestDailyLossCircuitBreaker:
    def test_pass_no_loss(self):
        rule = DailyLossCircuitBreaker(FakeRiskConfig())
        state = FakeDailyRiskState(realised_pnl=0)
        portfolio = Portfolio(current_balance=100000, peak_balance=100000)
        result = rule.check(make_signal(), state, portfolio)
        assert result.status == "PASS"

    def test_pass_small_loss(self):
        rule = DailyLossCircuitBreaker(FakeRiskConfig())
        state = FakeDailyRiskState(realised_pnl=-200)
        portfolio = Portfolio(current_balance=100000, peak_balance=100000)
        result = rule.check(make_signal(), state, portfolio)
        assert result.status == "PASS"

    def test_block_daily_limit_reached(self):
        rule = DailyLossCircuitBreaker(FakeRiskConfig())
        state = FakeDailyRiskState(realised_pnl=-500)
        portfolio = Portfolio(current_balance=100000, peak_balance=100000)
        result = rule.check(make_signal(), state, portfolio)
        assert result.status == "BLOCKED"
        assert result.reason == "DAILY_LOSS_LIMIT_REACHED"

    def test_block_pct_limit_lower_than_inr(self):
        """When pct cap (2% of 10k = 200) < INR cap (500), use pct cap."""
        rule = DailyLossCircuitBreaker(FakeRiskConfig())
        state = FakeDailyRiskState(realised_pnl=-200)
        portfolio = Portfolio(current_balance=10000, peak_balance=10000)
        result = rule.check(make_signal(), state, portfolio)
        assert result.status == "BLOCKED"

    def test_pass_with_profit(self):
        rule = DailyLossCircuitBreaker(FakeRiskConfig())
        state = FakeDailyRiskState(realised_pnl=1000)
        portfolio = Portfolio(current_balance=100000, peak_balance=100000)
        result = rule.check(make_signal(), state, portfolio)
        assert result.status == "PASS"


# ─── Rule 2: Account Drawdown Halt ──────────────────────────

class TestAccountDrawdownHalt:
    def test_pass_no_drawdown(self):
        rule = AccountDrawdownHalt(FakeRiskConfig())
        state = FakeDailyRiskState()
        portfolio = Portfolio(current_balance=100000, peak_balance=100000)
        result = rule.check(make_signal(), state, portfolio)
        assert result.status == "PASS"

    def test_pass_small_drawdown(self):
        rule = AccountDrawdownHalt(FakeRiskConfig())
        state = FakeDailyRiskState()
        portfolio = Portfolio(current_balance=95000, peak_balance=100000)
        result = rule.check(make_signal(), state, portfolio)
        assert result.status == "PASS"

    def test_block_at_10pct_drawdown(self):
        rule = AccountDrawdownHalt(FakeRiskConfig())
        state = FakeDailyRiskState()
        portfolio = Portfolio(current_balance=90000, peak_balance=100000)
        result = rule.check(make_signal(), state, portfolio)
        assert result.status == "BLOCKED"
        assert result.reason == "ACCOUNT_DRAWDOWN_HALT"

    def test_block_severe_drawdown(self):
        rule = AccountDrawdownHalt(FakeRiskConfig())
        state = FakeDailyRiskState()
        portfolio = Portfolio(current_balance=80000, peak_balance=100000)
        result = rule.check(make_signal(), state, portfolio)
        assert result.status == "BLOCKED"


# ─── Rule 3: Cooldown Filter ────────────────────────────────

class TestCooldownFilter:
    def test_pass_no_previous_signal(self):
        rule = CooldownFilter(FakeRiskConfig())
        state = FakeDailyRiskState(last_signal_time=None)
        portfolio = Portfolio(current_balance=100000, peak_balance=100000)
        result = rule.check(make_signal(), state, portfolio)
        assert result.status == "PASS"

    def test_pass_after_cooldown(self):
        rule = CooldownFilter(FakeRiskConfig())
        state = FakeDailyRiskState(
            last_signal_time=datetime.now(UTC) - timedelta(minutes=20)
        )
        portfolio = Portfolio(current_balance=100000, peak_balance=100000)
        result = rule.check(make_signal(), state, portfolio)
        assert result.status == "PASS"

    def test_skip_during_cooldown(self):
        rule = CooldownFilter(FakeRiskConfig())
        state = FakeDailyRiskState(
            last_signal_time=datetime.now(UTC) - timedelta(minutes=5)
        )
        portfolio = Portfolio(current_balance=100000, peak_balance=100000)
        result = rule.check(make_signal(), state, portfolio)
        assert result.status == "SKIPPED"
        assert result.reason == "COOLDOWN_ACTIVE"


# ─── Rule 4: Volatility Filter ──────────────────────────────

class TestVolatilityFilter:
    def test_pass_normal_volatility(self):
        rule = VolatilityFilter(FakeRiskConfig())
        state = FakeDailyRiskState()
        portfolio = Portfolio(current_balance=100000, peak_balance=100000)
        # ATR = 2.0, price = 100 → ATR% = 2.0% (within 0.3%-3.0%)
        result = rule.check(make_signal(atr_value=2.0), state, portfolio)
        assert result.status == "PASS"

    def test_skip_low_volatility(self):
        rule = VolatilityFilter(FakeRiskConfig())
        state = FakeDailyRiskState()
        portfolio = Portfolio(current_balance=100000, peak_balance=100000)
        # ATR = 0.1, price = 100 → ATR% = 0.1% (below 0.3%)
        result = rule.check(make_signal(atr_value=0.1), state, portfolio)
        assert result.status == "SKIPPED"
        assert result.reason == "LOW_VOLATILITY"

    def test_skip_high_volatility(self):
        rule = VolatilityFilter(FakeRiskConfig())
        state = FakeDailyRiskState()
        portfolio = Portfolio(current_balance=100000, peak_balance=100000)
        # ATR = 5.0, price = 100 → ATR% = 5.0% (above 3.0%)
        result = rule.check(make_signal(atr_value=5.0), state, portfolio)
        assert result.status == "SKIPPED"
        assert result.reason == "HIGH_VOLATILITY"


# ─── Rule 5: Position Sizer ─────────────────────────────────

class TestPositionSizer:
    def test_compute_quantity(self):
        rule = PositionSizer(FakeRiskConfig())
        state = FakeDailyRiskState()
        portfolio = Portfolio(current_balance=100000, peak_balance=100000)
        # Risk = 1% of 100k = 1000₹, stop_distance = 2₹ → qty = 500
        signal = make_signal(entry_price=100, stop_loss=98)
        result = rule.check(signal, state, portfolio)
        assert result.status == "PASS"
        assert result.quantity == 500
        assert result.risk_amount == 1000.0

    def test_block_insufficient_capital(self):
        rule = PositionSizer(FakeRiskConfig())
        state = FakeDailyRiskState()
        portfolio = Portfolio(current_balance=100, peak_balance=100)
        # Risk = 1% of 100 = 1₹, stop_distance = 2₹ → qty = 0
        signal = make_signal(entry_price=100, stop_loss=98)
        result = rule.check(signal, state, portfolio)
        assert result.status == "BLOCKED"
        assert result.reason == "INSUFFICIENT_CAPITAL"

    def test_block_zero_stop_distance(self):
        rule = PositionSizer(FakeRiskConfig())
        state = FakeDailyRiskState()
        portfolio = Portfolio(current_balance=100000, peak_balance=100000)
        signal = make_signal(entry_price=100, stop_loss=100)  # SL = entry
        result = rule.check(signal, state, portfolio)
        assert result.status == "BLOCKED"
        assert result.reason == "INVALID_STOP_LOSS"


# ─── Rule 6: Max Position Size Cap ──────────────────────────

class TestMaxPositionSizeCap:
    def test_no_cap_needed(self):
        rule = MaxPositionSizeCap(FakeRiskConfig())
        portfolio = Portfolio(current_balance=100000, peak_balance=100000)
        signal = make_signal(entry_price=100)
        # qty=100, position_value = 10k (10% < 20% cap)
        result = rule.adjust_quantity(100, signal, portfolio)
        assert result == 100

    def test_quantity_capped(self):
        rule = MaxPositionSizeCap(FakeRiskConfig())
        portfolio = Portfolio(current_balance=100000, peak_balance=100000)
        signal = make_signal(entry_price=100)
        # qty=500, position_value = 50k (50% > 20% cap) → cap to 200
        result = rule.adjust_quantity(500, signal, portfolio)
        assert result == 200


# ─── Rule 7: Max Concurrent Positions ────────────────────────

class TestMaxConcurrentPositions:
    def test_pass_no_positions(self):
        rule = MaxConcurrentPositions(FakeRiskConfig())
        state = FakeDailyRiskState()
        portfolio = Portfolio(current_balance=100000, peak_balance=100000, open_positions=0)
        result = rule.check(make_signal(), state, portfolio)
        assert result.status == "PASS"

    def test_pass_one_position(self):
        rule = MaxConcurrentPositions(FakeRiskConfig())
        state = FakeDailyRiskState()
        portfolio = Portfolio(current_balance=100000, peak_balance=100000, open_positions=1)
        result = rule.check(make_signal(), state, portfolio)
        assert result.status == "PASS"

    def test_skip_at_max(self):
        rule = MaxConcurrentPositions(FakeRiskConfig())
        state = FakeDailyRiskState()
        portfolio = Portfolio(current_balance=100000, peak_balance=100000, open_positions=2)
        result = rule.check(make_signal(), state, portfolio)
        assert result.status == "SKIPPED"
        assert result.reason == "MAX_POSITIONS_OPEN"


# ─── Full Engine Integration ─────────────────────────────────

class TestRiskEngineIntegration:
    def test_approved_signal(self):
        """Happy path — signal passes all 7 rules."""
        config = FakeRiskConfig()
        engine = RiskEngine(config)
        engine.load_adv({"1": 1000000})
        state = FakeDailyRiskState(realised_pnl=0, last_signal_time=None)
        portfolio = Portfolio(current_balance=100000, peak_balance=100000, open_positions=0)
        signal = make_signal(entry_price=100, stop_loss=98, target_price=104, atr_value=2.0)

        decision = engine.validate(signal, state, portfolio)

        assert decision.status == "APPROVED"
        assert decision.quantity is not None
        assert decision.quantity > 0
        assert decision.risk_amount is not None

    def test_blocked_by_daily_loss(self):
        config = FakeRiskConfig()
        engine = RiskEngine(config)
        state = FakeDailyRiskState(realised_pnl=-600)
        portfolio = Portfolio(current_balance=100000, peak_balance=100000)
        signal = make_signal()

        decision = engine.validate(signal, state, portfolio)

        assert decision.status == "BLOCKED"
        assert decision.reason == "DAILY_LOSS_LIMIT_REACHED"

    def test_blocked_by_drawdown(self):
        config = FakeRiskConfig()
        engine = RiskEngine(config)
        state = FakeDailyRiskState()
        portfolio = Portfolio(current_balance=85000, peak_balance=100000)
        signal = make_signal()

        decision = engine.validate(signal, state, portfolio)

        assert decision.status == "BLOCKED"
        assert decision.reason == "ACCOUNT_DRAWDOWN_HALT"

    def test_skipped_by_cooldown(self):
        config = FakeRiskConfig()
        engine = RiskEngine(config)
        state = FakeDailyRiskState(
            last_signal_time=datetime.now(UTC) - timedelta(minutes=2)
        )
        portfolio = Portfolio(current_balance=100000, peak_balance=100000)
        signal = make_signal()

        decision = engine.validate(signal, state, portfolio)

        assert decision.status == "SKIPPED"
        assert decision.reason == "COOLDOWN_ACTIVE"

    def test_position_cap_adjustment(self):
        """When raw qty exceeds cap, it should be adjusted down but still approved."""
        config = FakeRiskConfig()
        engine = RiskEngine(config)
        engine.load_adv({"1": 1000000})
        state = FakeDailyRiskState()
        portfolio = Portfolio(current_balance=100000, peak_balance=100000, open_positions=0)
        # Very tight stop → large qty → should hit position cap
        signal = make_signal(entry_price=100, stop_loss=99.9, target_price=100.3, atr_value=1.0)

        decision = engine.validate(signal, state, portfolio)

        assert decision.status == "APPROVED"
        # Cap: 20% of 100k / 100 = 200 shares max
        assert decision.quantity <= 200
