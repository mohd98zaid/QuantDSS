"""
TradingModeController — Single source of truth for execution mode routing.

Supports three trading modes:
  DISABLED — signals are dropped after Risk Engine; no trades created.
  PAPER    — simulated trades recorded in PaperTrade table; no broker call.
  LIVE     — real orders sent via ExecutionManager / broker API.

Safety locks for LIVE mode (BOTH must be satisfied):
  1. AutoTradeConfig.mode == "live"  (DB — set via API)
  2. env var LIVE_TRADING_LOCK == "CONFIRMED"  (server config — set in .env)

This double-gate prevents accidental live trading from a UI click alone.
"""
from __future__ import annotations

import os
from enum import Enum
from typing import TYPE_CHECKING

from app.core.logging import logger

if TYPE_CHECKING:
    from app.models.auto_trade_config import AutoTradeConfig
    from app.models.daily_risk_state import DailyRiskState


# ──────────────────────────────────────────────────────────────────────────────
# Enum
# ──────────────────────────────────────────────────────────────────────────────

class TradingMode(str, Enum):
    """
    The three operating modes of the QuantDSS execution engine.

    Inherits from `str` so values can be compared directly to strings and
    stored transparently in JSON / SQLAlchemy String columns.
    """
    DISABLED = "disabled"
    PAPER    = "paper"
    LIVE     = "live"

    @classmethod
    def from_str(cls, value: str) -> "TradingMode":
        """Case-insensitive parse. Falls back to PAPER on unknown values."""
        try:
            return cls(value.lower())
        except ValueError:
            logger.warning(
                f"TradingMode: unknown mode '{value}' — defaulting to PAPER"
            )
            return cls.PAPER


# ──────────────────────────────────────────────────────────────────────────────
# Safety Lock
# ──────────────────────────────────────────────────────────────────────────────

_LIVE_LOCK_ENV_KEY   = "LIVE_TRADING_LOCK"
_LIVE_LOCK_ENV_VALUE = "CONFIRMED"


def is_live_lock_set() -> bool:
    """
    Returns True if the server-level safety env var is set.
    This is checked independently of the DB mode flag.
    """
    return os.environ.get(_LIVE_LOCK_ENV_KEY, "").strip() == _LIVE_LOCK_ENV_VALUE


def live_lock_hint() -> str:
    """Human-readable instruction for enabling LIVE mode."""
    return (
        f"Set the environment variable {_LIVE_LOCK_ENV_KEY}={_LIVE_LOCK_ENV_VALUE} "
        "in your .env file and restart the server to enable LIVE trading."
    )


# ──────────────────────────────────────────────────────────────────────────────
# Controller
# ──────────────────────────────────────────────────────────────────────────────

class TradingModeController:
    """
    Routing controller for post-risk-engine signal execution.

    All methods are stateless classmethods — the controller derives its
    decisions from the live DB config and env-var lock, so it always
    reflects the current state without needing to be instantiated.
    """

    # ── Mode Resolution ───────────────────────────────────────────────────────

    @classmethod
    def get_mode(cls, cfg: "AutoTradeConfig | None") -> TradingMode:
        """
        Return the effective TradingMode from AutoTradeConfig.

        Rules:
        - If config is None or auto-trader is not enabled → DISABLED.
        - If cfg.mode == "live" but LIVE_TRADING_LOCK is not set → PAPER
          (with a warning log — this is a silent safety downgrade).
        - Otherwise: parse cfg.mode as TradingMode.
        """
        if cfg is None:
            return TradingMode.DISABLED
        if not cfg.enabled:
            return TradingMode.DISABLED

        mode = TradingMode.from_str(cfg.mode or "paper")

        # Safety downgrade: LIVE without env-var lock → PAPER
        if mode == TradingMode.LIVE and not is_live_lock_set():
            logger.warning(
                "TradingModeController: LIVE mode requested but "
                f"{_LIVE_LOCK_ENV_KEY} env var is not set. "
                "Downgrading to PAPER for safety. "
                + live_lock_hint()
            )
            return TradingMode.PAPER

        return mode

    # ── Execution Decision ────────────────────────────────────────────────────

    @classmethod
    def should_execute(
        cls,
        cfg: "AutoTradeConfig | None",
        risk_state: "DailyRiskState | None" = None,
    ) -> bool:
        """
        Returns True if signals should proceed to trade creation.

        False if:
          - Mode is DISABLED
          - DailyRiskState.is_halted is True (emergency halt)
        """
        if risk_state is not None and getattr(risk_state, "is_halted", False):
            return False
        return cls.get_mode(cfg) != TradingMode.DISABLED

    @classmethod
    def route_execution(
        cls,
        cfg: "AutoTradeConfig | None",
        risk_state: "DailyRiskState | None" = None,
    ) -> str | None:
        """
        Returns the execution route:
          - None    → DISABLED (drop signal)
          - "paper" → Create PaperTrade (no broker call)
          - "live"  → Route to ExecutionManager (broker API)
        """
        if not cls.should_execute(cfg, risk_state):
            return None
        mode = cls.get_mode(cfg)
        if mode == TradingMode.LIVE:
            return "live"
        return "paper"

    # ── Logging Helpers ───────────────────────────────────────────────────────

    @classmethod
    def log_prefix(cls, cfg: "AutoTradeConfig | None") -> str:
        """Return a standardised log prefix like '[MODE:PAPER]'."""
        mode = cls.get_mode(cfg)
        return f"[MODE:{mode.value.upper()}]"

    @classmethod
    def log_execution(
        cls,
        cfg: "AutoTradeConfig | None",
        action: str,
        symbol: str,
        detail: str = "",
    ) -> None:
        """Emit a standardised execution log line."""
        prefix = cls.log_prefix(cfg)
        msg = f"AutoTrader {prefix}: {action} {symbol}"
        if detail:
            msg += f" — {detail}"
        logger.info(msg)

    @classmethod
    def log_disabled_drop(cls, symbol: str, signal: str, reason: str = "") -> None:
        """Log a signal dropped because trading mode is DISABLED."""
        logger.info(
            f"AutoTrader [MODE:DISABLED]: DROP {signal} {symbol}"
            + (f" — {reason}" if reason else "")
        )

    # ── API Validation ────────────────────────────────────────────────────────

    @classmethod
    def validate_mode_switch(cls, requested_mode: str) -> tuple[bool, str]:
        """
        Validate a mode-switch request from the API.

        Returns (is_valid, error_message).
        error_message is empty string if valid.
        """
        try:
            mode = TradingMode(requested_mode.lower())
        except ValueError:
            valid = [m.value for m in TradingMode]
            return False, f"Invalid mode '{requested_mode}'. Must be one of: {valid}"

        if mode == TradingMode.LIVE and not is_live_lock_set():
            return False, (
                "Cannot switch to LIVE mode: server safety lock is not set. "
                + live_lock_hint()
            )

        return True, ""

    @classmethod
    def get_status(cls, cfg: "AutoTradeConfig | None") -> dict:
        """
        Return a complete status dict suitable for the API response.
        """
        mode = cls.get_mode(cfg)
        return {
            "current_mode": mode.value,
            "db_mode": getattr(cfg, "mode", "paper") if cfg else "paper",
            "enabled": bool(cfg and cfg.enabled),
            "live_lock_set": is_live_lock_set(),
            "live_available": is_live_lock_set(),
            "safety_downgraded": (
                getattr(cfg, "mode", "paper") == "live"
                and not is_live_lock_set()
            ),
        }


# Module-level singleton (stateless — methods are classmethods)
trading_mode_controller = TradingModeController()
