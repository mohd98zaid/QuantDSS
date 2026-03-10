"""
Meta-Strategy Engine — Architecture Rule 4 Enforcement.

Controls which strategies are active at any given time. Sits between the
Consolidation Layer and the Confirmation Layer in the intelligence pipeline.

Responsibilities:
  1. Check StrategyHealthMonitor: auto-disabled strategies are blocked
  2. Check RegimeDetector: strategies incompatible with current regime are blocked
  3. Forward only valid signals to the next layer

Signals from disabled/blocked strategies are logged but not forwarded.
"""
from typing import Callable, Awaitable

from app.core.logging import logger
from app.engine.consolidation_layer import ConsolidatedSignal
from app.engine.signal_trace import SignalTracer


class MetaStrategyEngine:
    """
    Filters consolidated signals based on strategy health and market regime.

    Signals must pass two gates:
      1. StrategyHealthMonitor: is the strategy auto-disabled due to poor performance?
      2. RegimeDetector: is the strategy allowed under the current market regime?

    If ALL contributing strategies in a ConsolidatedSignal are blocked,
    the signal is dropped entirely.
    """

    def __init__(self):
        self._next_callback: Callable[[ConsolidatedSignal], Awaitable[None]] | None = None

    def set_callback(self, callback: Callable[[ConsolidatedSignal], Awaitable[None]]):
        """Set the next layer in the pipeline (Confirmation Layer)."""
        self._next_callback = callback

    async def filter_signal(self, signal: ConsolidatedSignal):
        """
        Evaluate a ConsolidatedSignal against strategy health and regime rules.

        Drops the signal if all contributing strategies are disabled/blocked.
        If some strategies pass and some don't, strips the blocked ones and
        forwards with the remaining.
        """
        from app.engine.strategy_health import strategy_health_monitor
        from app.engine.regime_detector import RegimeDetector

        sym_name = getattr(signal, "symbol_name", "?")
        trace_id = getattr(signal, "_trace_id", "")

        # ── Get current market regime from DB cache ──────────────────────
        current_regime = await self._get_current_regime()

        # ── Filter contributing strategies ───────────────────────────────
        allowed_strategies: dict = {}
        blocked_reasons: list[str] = []

        for strat_name, candidate in signal.contributing_signals.items():
            strat_id = candidate.strategy_id

            # Gate 1: Strategy Health
            if await strategy_health_monitor.is_disabled(strat_id):
                blocked_reasons.append(
                    f"{strat_name}(id={strat_id}): disabled by health monitor"
                )
                continue

            # Gate 2: Market Regime
            #   Use the strategy type key to check regime compatibility.
            #   CandidateSignal.strategy_name may be human-readable; derive
            #   the registry key from the base_strategy or strategy_name.
            strat_key = getattr(candidate, "base_strategy", strat_name).lower()
            strat_key = strat_key.replace(" ", "_")
            if not RegimeDetector.is_strategy_allowed(strat_key, current_regime):
                blocked_reasons.append(
                    f"{strat_name}: blocked by regime={current_regime}"
                )
                continue

            allowed_strategies[strat_name] = candidate

        # ── Decision ─────────────────────────────────────────────────────
        if not allowed_strategies:
            # All strategies blocked — drop the signal
            logger.info(
                f"MetaStrategy BLOCKED {signal.signal_type} for {sym_name}: "
                f"all strategies filtered. Reasons: {blocked_reasons}"
            )
            SignalTracer.trace_drop(
                trace_id, "META_STRATEGY", sym_name,
                f"All strategies blocked: {blocked_reasons}"
            )
            return

        if blocked_reasons:
            # Some blocked, some allowed — strip blocked and forward
            logger.info(
                f"MetaStrategy: stripped {len(blocked_reasons)} strategy(ies) "
                f"from {sym_name} signal. Remaining: {list(allowed_strategies.keys())}"
            )
            signal.contributing_signals = allowed_strategies

        SignalTracer.trace_pass(
            trace_id, "META_STRATEGY", sym_name,
            f"{len(allowed_strategies)} strategy(ies) active, regime={current_regime}"
        )

        # Forward to Confirmation Layer
        if self._next_callback:
            try:
                await self._next_callback(signal)
            except Exception as e:
                logger.exception(
                    f"Error in next layer callback from MetaStrategy: {e}"
                )

    async def _get_current_regime(self) -> str:
        """
        Fetch the current market regime from RiskConfig (set by regime_scheduler).
        Falls back to 'TREND' if unavailable.
        """
        try:
            from app.core.database import async_session_factory
            from app.models.risk_config import RiskConfig
            from sqlalchemy import select

            async with async_session_factory() as db:
                result = await db.execute(select(RiskConfig).limit(1))
                cfg = result.scalar_one_or_none()
                if cfg and hasattr(cfg, "market_regime") and cfg.market_regime:
                    return str(cfg.market_regime)
        except Exception as e:
            logger.debug(f"MetaStrategy: regime lookup failed ({e}), defaulting to TREND")
        return "TREND"


# Module-level singleton
meta_strategy_engine = MetaStrategyEngine()
