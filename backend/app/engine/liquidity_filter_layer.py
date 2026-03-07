"""
Liquidity Filter Layer — Intelligence Pipeline Layer.

Final filter before FinalAlertGenerator. Ensures sufficient market liquidity
exists to enter and exit the trade without excessive slippage.

Checks:
  1. Volume ratio >= min_volume_ratio (default 0.5x average volume)
  2. Spread <= max_spread_pct (default 0.5% of entry price)
"""
from typing import Callable, Awaitable

from app.core.logging import logger
from app.engine.consolidation_layer import ConsolidatedSignal
from app.engine.signal_trace import SignalTracer


class LiquidityFilterLayer:
    """
    Blocks signals in illiquid conditions to prevent slippage risk.
    """

    def __init__(
        self,
        min_volume_ratio: float = 0.5,
        max_spread_pct: float = 0.005,  # 0.5%
    ):
        self.min_volume_ratio = min_volume_ratio
        self.max_spread_pct = max_spread_pct
        self._next_callback: Callable[[ConsolidatedSignal], Awaitable[None]] | None = None

    def set_callback(self, callback: Callable[[ConsolidatedSignal], Awaitable[None]]):
        """Set the next layer (FinalAlertGenerator)."""
        self._next_callback = callback

    async def evaluate(self, signal: ConsolidatedSignal):
        """Check volume and spread conditions."""
        sym_name = getattr(signal, "symbol_name", "?")
        trace_id = getattr(signal, "_trace_id", "")

        # Get volume ratio from primary signal
        primary = list(signal.contributing_signals.values())[0]
        volume_ratio = getattr(primary, "volume_ratio", 1.0) or 1.0
        spread = getattr(primary, "spread", 0.0) or 0.0

        # ── Volume check ─────────────────────────────────────────────
        if volume_ratio < self.min_volume_ratio:
            logger.info(
                f"LiquidityFilter BLOCKED {sym_name} ({signal.signal_type}): "
                f"volume_ratio {volume_ratio:.2f} < {self.min_volume_ratio}"
            )
            SignalTracer.trace_drop(
                trace_id, "LIQUIDITY_FILTER", sym_name,
                f"Low volume: ratio={volume_ratio:.2f}"
            )
            return

        # ── Spread check ─────────────────────────────────────────────
        if spread > 0 and spread > self.max_spread_pct:
            logger.info(
                f"LiquidityFilter BLOCKED {sym_name} ({signal.signal_type}): "
                f"spread {spread:.4f} > {self.max_spread_pct}"
            )
            SignalTracer.trace_drop(
                trace_id, "LIQUIDITY_FILTER", sym_name,
                f"Wide spread: {spread:.4f}"
            )
            return

        # ── Passed — forward to FinalAlertGenerator ──────────────────
        SignalTracer.trace_pass(
            trace_id, "LIQUIDITY_FILTER", sym_name,
            f"vol_ratio={volume_ratio:.2f}, spread={spread:.4f}"
        )

        if self._next_callback:
            try:
                await self._next_callback(signal)
            except Exception as e:
                logger.exception(
                    f"Error in next layer callback from LiquidityFilter: {e}"
                )


# Module-level singleton
liquidity_filter_layer = LiquidityFilterLayer()
