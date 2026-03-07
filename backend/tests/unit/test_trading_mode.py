"""
Tests for TradingModeController.

Tests cover:
  - TradingMode enum parsing
  - Mode resolution logic (DISABLED when cfg=None, when enabled=False)
  - LIVE safety downgrades (no env lock → PAPER)
  - route_execution() return values
  - validate_mode_switch() API validation
  - get_status() status dict
"""
import os
import pytest
from unittest.mock import MagicMock, patch


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_cfg(mode: str = "paper", enabled: bool = True):
    """Build a minimal AutoTradeConfig-like mock."""
    cfg = MagicMock()
    cfg.mode = mode
    cfg.enabled = enabled
    return cfg


def _make_risk_state(is_halted: bool = False):
    rs = MagicMock()
    rs.is_halted = is_halted
    return rs


# ── TradingMode enum ──────────────────────────────────────────────────────────

def test_trading_mode_from_str_valid():
    from app.engine.trading_mode import TradingMode
    assert TradingMode.from_str("paper") == TradingMode.PAPER
    assert TradingMode.from_str("PAPER") == TradingMode.PAPER
    assert TradingMode.from_str("live") == TradingMode.LIVE
    assert TradingMode.from_str("disabled") == TradingMode.DISABLED


def test_trading_mode_from_str_unknown_defaults_to_paper():
    from app.engine.trading_mode import TradingMode
    assert TradingMode.from_str("unknown_mode") == TradingMode.PAPER


def test_trading_mode_is_str():
    """TradingMode values are plain strings — compare without .value."""
    from app.engine.trading_mode import TradingMode
    assert TradingMode.PAPER == "paper"
    assert TradingMode.LIVE == "live"
    assert TradingMode.DISABLED == "disabled"


# ── get_mode() resolution ─────────────────────────────────────────────────────

def test_get_mode_none_cfg_returns_disabled():
    from app.engine.trading_mode import TradingModeController, TradingMode
    assert TradingModeController.get_mode(None) == TradingMode.DISABLED


def test_get_mode_disabled_when_not_enabled():
    from app.engine.trading_mode import TradingModeController, TradingMode
    cfg = _make_cfg(mode="paper", enabled=False)
    assert TradingModeController.get_mode(cfg) == TradingMode.DISABLED


def test_get_mode_paper():
    from app.engine.trading_mode import TradingModeController, TradingMode
    cfg = _make_cfg(mode="paper", enabled=True)
    assert TradingModeController.get_mode(cfg) == TradingMode.PAPER


def test_get_mode_disabled_explicit():
    from app.engine.trading_mode import TradingModeController, TradingMode
    cfg = _make_cfg(mode="disabled", enabled=True)
    # Even with enabled=True, explicit DISABLED mode returns DISABLED
    assert TradingModeController.get_mode(cfg) == TradingMode.DISABLED


def test_get_mode_live_without_lock_downgrades_to_paper():
    """LIVE mode without env var lock must silently downgrade to PAPER."""
    from app.engine.trading_mode import TradingModeController, TradingMode
    cfg = _make_cfg(mode="live", enabled=True)
    # Ensure env var is NOT set
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("LIVE_TRADING_LOCK", None)
        result = TradingModeController.get_mode(cfg)
    assert result == TradingMode.PAPER


def test_get_mode_live_with_lock():
    """LIVE mode with correct env var should return LIVE."""
    from app.engine.trading_mode import TradingModeController, TradingMode
    cfg = _make_cfg(mode="live", enabled=True)
    with patch.dict(os.environ, {"LIVE_TRADING_LOCK": "CONFIRMED"}):
        result = TradingModeController.get_mode(cfg)
    assert result == TradingMode.LIVE


# ── should_execute() ──────────────────────────────────────────────────────────

def test_should_execute_disabled_returns_false():
    from app.engine.trading_mode import TradingModeController
    cfg = _make_cfg(mode="disabled", enabled=True)
    assert TradingModeController.should_execute(cfg) is False


def test_should_execute_halted_returns_false():
    from app.engine.trading_mode import TradingModeController
    cfg = _make_cfg(mode="paper", enabled=True)
    risk_state = _make_risk_state(is_halted=True)
    assert TradingModeController.should_execute(cfg, risk_state) is False


def test_should_execute_paper_not_halted_returns_true():
    from app.engine.trading_mode import TradingModeController
    cfg = _make_cfg(mode="paper", enabled=True)
    assert TradingModeController.should_execute(cfg) is True


# ── route_execution() ─────────────────────────────────────────────────────────

def test_route_execution_disabled_returns_none():
    from app.engine.trading_mode import TradingModeController
    cfg = _make_cfg(mode="disabled", enabled=True)
    assert TradingModeController.route_execution(cfg) is None


def test_route_execution_paper_returns_paper():
    from app.engine.trading_mode import TradingModeController
    cfg = _make_cfg(mode="paper", enabled=True)
    assert TradingModeController.route_execution(cfg) == "paper"


def test_route_execution_live_with_lock_returns_live():
    from app.engine.trading_mode import TradingModeController
    cfg = _make_cfg(mode="live", enabled=True)
    with patch.dict(os.environ, {"LIVE_TRADING_LOCK": "CONFIRMED"}):
        result = TradingModeController.route_execution(cfg)
    assert result == "live"


def test_route_execution_live_without_lock_returns_paper():
    """Safety downgrade: LIVE without lock falls back to paper execution route."""
    from app.engine.trading_mode import TradingModeController
    cfg = _make_cfg(mode="live", enabled=True)
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("LIVE_TRADING_LOCK", None)
        result = TradingModeController.route_execution(cfg)
    assert result == "paper"


# ── validate_mode_switch() ────────────────────────────────────────────────────

def test_validate_mode_switch_valid_modes():
    from app.engine.trading_mode import TradingModeController
    for mode in ("paper", "disabled", "PAPER"):
        is_valid, err = TradingModeController.validate_mode_switch(mode)
        assert is_valid, f"Expected valid for mode='{mode}', got err='{err}'"


def test_validate_mode_switch_live_without_lock_is_invalid():
    from app.engine.trading_mode import TradingModeController
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("LIVE_TRADING_LOCK", None)
        is_valid, err = TradingModeController.validate_mode_switch("live")
    assert is_valid is False
    assert "LIVE_TRADING_LOCK" in err


def test_validate_mode_switch_live_with_lock_is_valid():
    from app.engine.trading_mode import TradingModeController
    with patch.dict(os.environ, {"LIVE_TRADING_LOCK": "CONFIRMED"}):
        is_valid, err = TradingModeController.validate_mode_switch("live")
    assert is_valid is True
    assert err == ""


def test_validate_mode_switch_unknown_is_invalid():
    from app.engine.trading_mode import TradingModeController
    is_valid, err = TradingModeController.validate_mode_switch("turbo")
    assert is_valid is False


# ── get_status() ──────────────────────────────────────────────────────────────

def test_get_status_paper():
    from app.engine.trading_mode import TradingModeController
    cfg = _make_cfg(mode="paper", enabled=True)
    status = TradingModeController.get_status(cfg)
    assert status["current_mode"] == "paper"
    assert status["enabled"] is True
    assert status["safety_downgraded"] is False


def test_get_status_live_downgraded():
    from app.engine.trading_mode import TradingModeController
    cfg = _make_cfg(mode="live", enabled=True)
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("LIVE_TRADING_LOCK", None)
        status = TradingModeController.get_status(cfg)
    assert status["db_mode"] == "live"
    assert status["current_mode"] == "paper"
    assert status["safety_downgraded"] is True


# ── log helpers (smoke tests) ─────────────────────────────────────────────────

def test_log_prefix_paper():
    from app.engine.trading_mode import TradingModeController
    cfg = _make_cfg(mode="paper", enabled=True)
    assert TradingModeController.log_prefix(cfg) == "[MODE:PAPER]"


def test_log_prefix_disabled():
    from app.engine.trading_mode import TradingModeController
    assert TradingModeController.log_prefix(None) == "[MODE:DISABLED]"
