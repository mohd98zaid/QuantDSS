"""
Market Regime Filter — Intelligence Pipeline Layer.

Sits between Quality Score and ML Filter. Uses the RegimeDetector's
classification (stored in RiskConfig.market_regime by regime_scheduler)
to block signals that conflict with the current regime.

Unlike MetaStrategyEngine (which filters individual strategies), this
layer examines the overall signal quality relative to the regime.
For example, in HIGH_VOLATILITY regime, only signals with quality_score >= 85
are allowed through.
"""
from typing import Callable, Awaitable

from app.core.logging import logger
from app.engine.consolidation_layer import ConsolidatedSignal
from app.engine.signal_trace import SignalTracer


class MarketRegimeFilter:
    """
    Applies regime-specific quality thresholds to signals.

    Regimes and their effects:
      - TREND:            All signals pass (no extra filter)
      - RANGE:            Only mean-reversion-family signals pass
      - HIGH_VOLATILITY:  Only signals with quality_score >= 85 pass
      - LOW_LIQUIDITY:    Block ALL signals (capital protection)
    """

    def __init__(self):
        self._next_callback: Callable[[ConsolidatedSignal], Awaitable[None]] | None = None

    def set_callback(self, callback: Callable[[ConsolidatedSignal], Awaitable[None]]):
        """Set the next layer in the pipeline (ML Filter)."""
        self._next_callback = callback

    async def evaluate(self, signal: ConsolidatedSignal):
        """Apply regime-based filtering to the signal."""
        sym_name = getattr(signal, "symbol_name", "?")
        trace_id = getattr(signal, "_trace_id", "")

        regime = await self._get_regime()
        score = getattr(signal, "quality_score", 0.0) or 0.0

        # ── LOW_LIQUIDITY: Block everything ──────────────────────────
        if regime == "LOW_LIQUIDITY":
            logger.info(
                f"MarketRegimeFilter BLOCKED {sym_name} ({signal.signal_type}): "
                f"LOW_LIQUIDITY regime — all signals blocked"
            )
            SignalTracer.trace_drop(
                trace_id, "REGIME_FILTER", sym_name, "LOW_LIQUIDITY"
            )
            return

        # ── HIGH_VOLATILITY: Require very high quality ───────────────
        if regime == "HIGH_VOLATILITY" and score < 85.0:
            logger.info(
                f"MarketRegimeFilter BLOCKED {sym_name} ({signal.signal_type}): "
                f"HIGH_VOLATILITY regime, score {score:.1f} < 85.0"
            )
            SignalTracer.trace_drop(
                trace_id, "REGIME_FILTER", sym_name,
                f"HIGH_VOLATILITY, score {score:.1f} < 85"
            )
            return

        # ── RANGE: Only allow mean-reversion strategies ──────────────
        if regime == "RANGE":
            mr_keywords = {"rsi", "mean_reversion", "reversion"}
            strategies = getattr(signal, "contributing_strategies", [])
            has_mr = any(
                any(kw in s.lower() for kw in mr_keywords)
                for s in strategies
            )
            if not has_mr:
                logger.info(
                    f"MarketRegimeFilter BLOCKED {sym_name} ({signal.signal_type}): "
                    f"RANGE regime, no mean-reversion strategy among {strategies}"
                )
                SignalTracer.trace_drop(
                    trace_id, "REGIME_FILTER", sym_name,
                    f"RANGE regime, strategies={strategies}"
                )
                return

        # ── TREND or passed all checks — forward ─────────────────────
        SignalTracer.trace_pass(
            trace_id, "REGIME_FILTER", sym_name,
            f"regime={regime}, score={score:.1f}"
        )

        if self._next_callback:
            try:
                await self._next_callback(signal)
            except Exception as e:
                logger.exception(
                    f"Error in next layer callback from MarketRegimeFilter: {e}"
                )

    async def _get_regime(self) -> str:
        """Fetch current regime from RiskConfig."""
        try:
            from app.core.database import async_session_factory
            from app.models.risk_config import RiskConfig
            from sqlalchemy import select

            async with async_session_factory() as db:
                result = await db.execute(select(RiskConfig).limit(1))
                cfg = result.scalar_one_or_none()
                if cfg and hasattr(cfg, "market_regime") and cfg.market_regime:
                    regime = str(cfg.market_regime)
                    if regime != "NONE":
                        return regime
        except Exception as e:
            logger.debug(f"MarketRegimeFilter: regime lookup failed ({e})")
        return "TREND"


# Module-level singleton
market_regime_filter = MarketRegimeFilter()
