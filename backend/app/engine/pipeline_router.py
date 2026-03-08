"""
PipelineRouter — Fast/Slow path split for signal intelligence pipeline.

Splits the 11-layer intelligence pipeline into:
  FAST PATH (<200ms target):
    1. Signal Deduplication
    2. Spread Filter (from Liquidity)
    3. Liquidity Filter
    4. Basic Market Regime
    5. Risk Engine validation
    → Publishes approved signals immediately

  SLOW PATH (async, non-blocking):
    - ML Filter (shadow evaluation)
    - NLP Filter (shadow evaluation)
    - Meta-Strategy Analytics
    → Updates analytics/metrics but does NOT block execution

The slow path runs in shadow evaluation mode: it processes signals
asynchronously and logs results for analytics, but never prevents
a fast-path-approved signal from being executed.

Usage:
    router = PipelineRouter()
    await router.init()
    await router.route(candidate_signal)
"""
from __future__ import annotations

import asyncio
import time
from typing import Callable, Awaitable, Optional

from app.core.logging import logger


class PipelineRouter:
    """
    Routes signals through fast and slow pipeline paths.

    The fast path is synchronous (awaited inline).
    The slow path is fire-and-forget via asyncio.create_task().
    """

    def __init__(self):
        self._fast_layers: list[Callable] = []
        self._slow_layers: list[Callable] = []
        self._on_approved: Optional[Callable] = None
        self._initialized = False

    async def init(self, on_approved: Callable) -> None:
        """
        Wire the fast and slow pipeline layers.

        Args:
            on_approved: Terminal callback when a signal passes the fast path.
        """
        self._on_approved = on_approved
        self._initialized = True
        logger.info("PipelineRouter: Fast/slow pipeline initialized")

    def add_fast_layer(self, layer: Callable) -> None:
        """Add a layer to the fast path (synchronous, blocking)."""
        self._fast_layers.append(layer)

    def add_slow_layer(self, layer: Callable) -> None:
        """Add a layer to the slow path (async, non-blocking shadow mode)."""
        self._slow_layers.append(layer)

    async def route(self, signal) -> None:
        """
        Route a signal through the fast path, then fire slow path async.

        The fast path must complete under 200ms. If any fast layer rejects
        the signal (returns None/False), the signal is dropped.
        """
        if not self._initialized:
            logger.warning("PipelineRouter: Not initialized — dropping signal")
            return

        start_time = time.monotonic()

        # ── Fast Path ────────────────────────────────────────────
        current_signal = signal
        for layer in self._fast_layers:
            try:
                result = await layer(current_signal)
                if result is None or result is False:
                    elapsed_ms = (time.monotonic() - start_time) * 1000
                    logger.debug(
                        f"PipelineRouter: Signal rejected by fast path "
                        f"layer {layer.__name__} ({elapsed_ms:.1f}ms)"
                    )
                    return
                if result is not True:
                    current_signal = result
            except Exception as e:
                logger.exception(f"PipelineRouter: Fast path error in {layer.__name__}: {e}")
                return

        elapsed_ms = (time.monotonic() - start_time) * 1000

        # Track latency metrics
        try:
            from app.core.metrics import pipeline_latency
            pipeline_latency.observe(elapsed_ms)
        except Exception:
            pass

        if elapsed_ms > 200:
            logger.warning(
                f"PipelineRouter: Fast path exceeded 200ms target ({elapsed_ms:.1f}ms)"
            )
        else:
            logger.debug(f"PipelineRouter: Fast path completed in {elapsed_ms:.1f}ms")

        # ── Publish approved signal ──────────────────────────────
        if self._on_approved:
            await self._on_approved(current_signal)

        # ── Slow Path (non-blocking shadow evaluation) ───────────
        if self._slow_layers:
            asyncio.create_task(self._run_slow_path(current_signal))

    async def _run_slow_path(self, signal) -> None:
        """
        Execute slow path layers in shadow mode.

        Results are logged for analytics but do NOT affect the signal's
        execution status. Errors are caught and logged, never propagated.
        """
        for layer in self._slow_layers:
            try:
                result = await layer(signal)
                # Log shadow evaluation result
                layer_name = getattr(layer, "__name__", str(layer))
                logger.debug(
                    f"PipelineRouter: Slow path shadow — {layer_name} "
                    f"result={result}"
                )
            except Exception as e:
                layer_name = getattr(layer, "__name__", str(layer))
                logger.warning(
                    f"PipelineRouter: Slow path error in {layer_name}: {e}"
                )
