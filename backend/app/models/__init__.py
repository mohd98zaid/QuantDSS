# Import all models so Alembic can detect them
from app.models.audit_log import AuditLog
from app.models.backtest_run import BacktestRun, BacktestTrade
from app.models.candle import Candle
from app.models.daily_risk_state import DailyRiskState
from app.models.risk_config import RiskConfig
from app.models.signal import Signal
from app.models.strategy import Strategy, StrategySymbol
from app.models.symbol import Symbol
from app.models.trade import Trade
from app.models.paper_trade import PaperTrade
from app.models.auto_trade_log import AutoTradeLog
from app.models.auto_trade_config import AutoTradeConfig
from app.models.live_trade import LiveTrade
from app.models.strategy_health import StrategyHealth
from app.models.strategy_health_log import StrategyHealthLog
from app.models.kill_switch_event import KillSwitchEvent

__all__ = [
    "Symbol",
    "Strategy",
    "StrategySymbol",
    "Candle",
    "Signal",
    "Trade",
    "DailyRiskState",
    "RiskConfig",
    "BacktestRun",
    "BacktestTrade",
    "AuditLog",
    "PaperTrade",
    "LiveTrade",
    "StrategyHealth",
    "KillSwitchEvent",
]
