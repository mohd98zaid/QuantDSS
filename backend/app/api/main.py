"""
QuantDSS — FastAPI Application Factory
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routers import (
    auth,
    backtest,
    candles,
    health,
    market_data,
    performance,
    risk,
    scanner,
    signals,
    strategies,
    stream,
    symbols,
    trades,
    paper,
    auto_trader,
    admin,
    replay,
)
from app.core.config import settings
from app.core.logging import logger
from app.core.scheduler import scheduler, setup_scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown events."""
    # Startup
    logger.info("QuantDSS starting up...")

    # ── Critical Fix 4: Security validation — fail fast if credentials missing ─
    try:
        settings.validate_security()
        logger.info("Security: Credentials validated OK")
    except RuntimeError as _sec_err:
        logger.error(f"STARTUP ABORTED: {_sec_err}")
        raise

    # ── Ensure all DB tables exist (safe to call on every start) ────────────
    from app.core.database import engine, Base
    from sqlalchemy import text

    # Import every model so SQLAlchemy registers it before create_all
    import app.models.symbol        # noqa: F401
    import app.models.strategy      # noqa: F401
    import app.models.signal        # noqa: F401
    import app.models.trade         # noqa: F401
    import app.models.paper_trade   # noqa: F401
    import app.models.risk_config   # noqa: F401
    import app.models.daily_risk_state   # noqa: F401
    import app.models.audit_log          # noqa: F401
    import app.models.candle             # noqa: F401
    import app.models.backtest_run       # noqa: F401
    import app.models.auto_trade_config  # noqa: F401
    import app.models.auto_trade_log     # noqa: F401

    # Step 1: create all tables in their own committed transaction
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables verified / created")

    # Step 2: each inline migration runs in its OWN transaction so a
    # "column already exists" error cannot roll back the table creation above.
    migrations = [
        # auto_trade_config columns added in audit phase
        "ALTER TABLE auto_trade_config ADD COLUMN sizing_mode VARCHAR(10) DEFAULT 'capital' NOT NULL",
        "ALTER TABLE auto_trade_config ADD COLUMN capital_per_trade FLOAT DEFAULT 10000.0 NOT NULL",
        # risk_config new columns (Audit Phase 2 / Cat. 4,5,6)
        "ALTER TABLE risk_config ADD COLUMN max_position_pct NUMERIC(6,4) DEFAULT 0.20",
        "ALTER TABLE risk_config ADD COLUMN max_weekly_loss_inr NUMERIC(12,2) DEFAULT 2000.00",
        "ALTER TABLE risk_config ADD COLUMN max_weekly_loss_pct NUMERIC(6,4) DEFAULT 0.05",
        "ALTER TABLE risk_config ADD COLUMN min_risk_reward NUMERIC(5,2) DEFAULT 1.50",
        "ALTER TABLE risk_config ADD COLUMN max_signals_per_stock INTEGER DEFAULT 3",
        "ALTER TABLE risk_config ADD COLUMN signal_start_hour INTEGER DEFAULT 9",
        "ALTER TABLE risk_config ADD COLUMN signal_start_minute INTEGER DEFAULT 20",
        "ALTER TABLE risk_config ADD COLUMN signal_end_hour INTEGER DEFAULT 14",
        "ALTER TABLE risk_config ADD COLUMN signal_end_minute INTEGER DEFAULT 30",
        "ALTER TABLE risk_config ADD COLUMN max_consecutive_errors INTEGER DEFAULT 5",
        # risk_config additional columns from audit phase (live trading + liquidity)
        "ALTER TABLE risk_config ADD COLUMN max_correlated_positions INTEGER DEFAULT 3",
        "ALTER TABLE risk_config ADD COLUMN min_daily_volume INTEGER DEFAULT 500000",
        "ALTER TABLE risk_config ADD COLUMN max_spread_pct NUMERIC(6,4) DEFAULT 0.005",
        "ALTER TABLE risk_config ADD COLUMN market_regime VARCHAR(20) DEFAULT 'NONE'",
        "ALTER TABLE risk_config ADD COLUMN paper_balance NUMERIC(12,2) DEFAULT 100000.00",
        # Issue 4 Fix: order timeout configuration
        "ALTER TABLE risk_config ADD COLUMN order_timeout_minutes INTEGER DEFAULT 5",
        # Issue 5 Fix: risk_amount column on live_trade for exposure tracking
        "ALTER TABLE live_trade ADD COLUMN risk_amount NUMERIC(12,2) DEFAULT NULL",
        # Corrective Refactor: Intelligence pipeline metadata on signals
        "ALTER TABLE signals ADD COLUMN ml_probability NUMERIC(6,4) DEFAULT NULL",
        "ALTER TABLE signals ADD COLUMN sentiment VARCHAR(20) DEFAULT NULL",
        "ALTER TABLE signals ADD COLUMN strategies_confirmed TEXT DEFAULT NULL",
        # TradingModeController: tag every log entry and trade record with execution mode
        "ALTER TABLE auto_trade_log ADD COLUMN trading_mode VARCHAR(10) DEFAULT NULL",
        "ALTER TABLE paper_trades ADD COLUMN trading_mode VARCHAR(10) DEFAULT 'paper'",
        # MarketReplayEngine: tag paper trades with replay session ID
        "ALTER TABLE paper_trades ADD COLUMN replay_session_id VARCHAR(50) DEFAULT NULL",
    ]
    for stmt in migrations:
        try:
            async with engine.begin() as conn:
                await conn.execute(text(stmt))
        except Exception:
            pass  # column already exists — safe to ignore

    # Step 3: Seed auto_trade_config with defaults if table is empty
    from app.core.database import async_session_factory
    from app.models.auto_trade_config import AutoTradeConfig
    from sqlalchemy import select
    try:
        async with async_session_factory() as db:
            result = await db.execute(select(AutoTradeConfig).limit(1))
            if not result.scalar_one_or_none():
                db.add(AutoTradeConfig())   # all defaults: enabled=False
                await db.commit()
                logger.info("Auto-trader config seeded with defaults (disabled)")
    except Exception as _seed_err:
        logger.warning(f"Auto-trader config seed skipped (non-fatal): {_seed_err}")

    # Step 4: Seed default strategies if none exist
    from app.models.strategy import Strategy
    _DEFAULT_STRATEGIES = [
        {
            "name": "EMA Crossover",
            "type": "ema_crossover",
            "description": "Trend-following strategy. Enters when fast EMA crosses above/below slow EMA with volume confirmation.",
            "parameters": {
                "ema_fast": 9, "ema_slow": 21, "atr_period": 14,
                "atr_multiplier_sl": 1.5, "atr_multiplier_target": 3.0,
                "volume_ma_period": 20,
            },
        },
        {
            "name": "RSI Mean Reversion",
            "type": "rsi_mean_reversion",
            "description": "Pullback entries in established trends. Buys oversold bounces in uptrend; sells overbought rejections in downtrend.",
            "parameters": {
                "rsi_period": 14, "ema_trend": 50, "atr_period": 14,
                "rsi_oversold": 35, "rsi_overbought": 65,
                "atr_multiplier_sl": 1.0, "risk_reward": 2.0,
            },
        },
        {
            "name": "ORB VWAP",
            "type": "orb_vwap",
            "description": "Opening Range Breakout with VWAP filter. Trades breakouts of the first-candle range when price is on the correct side of VWAP.",
            "parameters": {
                "atr_period": 14, "atr_multiplier_sl": 1.0,
                "atr_multiplier_target": 2.0, "volume_ma_period": 20,
            },
        },
        {
            "name": "Trend Continuation",
            "type": "trend_continuation",
            "description": "Enters pullbacks within established trends using EMA structure and RSI confirmation.",
            "parameters": {
                "ema_fast": 9, "ema_slow": 21, "ema_trend": 50,
                "rsi_period": 14, "rsi_min": 40, "rsi_max": 60,
                "atr_period": 14, "atr_multiplier_sl": 1.5,
                "atr_multiplier_target": 3.0, "volume_ma_period": 20,
            },
        },
        {
            "name": "Volume Expansion",
            "type": "volume_expansion",
            "description": "Trades breakouts accompanied by significant volume expansion, filtering false moves with ATR.",
            "parameters": {
                "volume_ma_period": 20, "volume_multiplier": 2.0,
                "atr_period": 14, "atr_multiplier_sl": 1.5,
                "atr_multiplier_target": 3.0,
            },
        },
        {
            "name": "VWAP Reclaim",
            "type": "vwap_reclaim",
            "description": "Enters when price reclaims VWAP after a pullback, confirming with volume and trend.",
            "parameters": {
                "atr_period": 14, "atr_multiplier_sl": 1.0,
                "risk_reward": 2.0, "volume_ma_period": 20,
            },
        },
        {
            "name": "Relative Strength",
            "type": "relative_strength",
            "description": "Identifies stocks outperforming the benchmark index using relative strength analysis.",
            "parameters": {
                "atr_period": 14, "atr_multiplier_sl": 1.5,
                "risk_reward": 2.0, "lookback": 20,
            },
        },
        {
            "name": "Trend Pullback",
            "type": "trend_pullback",
            "description": "Catches pullback entries in established trends using EMA structure and RSI dip zones.",
            "parameters": {
                "ema_fast": 9, "ema_slow": 21, "ema_trend": 50,
                "rsi_period": 14, "atr_period": 14,
                "atr_multiplier_sl": 1.2, "risk_reward": 2.5,
            },
        },
        {
            "name": "Failed Breakout",
            "type": "failed_breakout",
            "description": "Fades breakout failures when volume doesn't confirm and price reverses quickly.",
            "parameters": {
                "lookback": 20, "atr_period": 14,
                "atr_multiplier_sl": 1.0, "risk_reward": 2.0,
                "volume_ma_period": 20,
            },
        },
    ]
    try:
        async with async_session_factory() as db:
            result = await db.execute(select(Strategy).limit(1))
            if not result.scalar_one_or_none():
                for s in _DEFAULT_STRATEGIES:
                    db.add(Strategy(**s))
                await db.commit()
                logger.info(f"Seeded {len(_DEFAULT_STRATEGIES)} default strategies")
    except Exception as _strat_err:
        logger.warning(f"Strategy seed skipped (non-fatal): {_strat_err}")

    setup_scheduler()

    # Fix C-04: Inline migration for new LiveTrade columns
    try:
        from sqlalchemy import text, inspect
        async with async_session_factory() as _mig_db:
            conn = await _mig_db.connection()
            raw = await conn.run_sync(lambda c: inspect(c).get_columns("live_trades"))
            col_names = {col["name"] for col in raw}
            for col_name in ["sl_order_id", "target_order_id", "risk_amount"]:
                if col_name not in col_names:
                    col_type = "FLOAT" if col_name == "risk_amount" else "VARCHAR(100)"
                    await _mig_db.execute(text(
                        f"ALTER TABLE live_trades ADD COLUMN {col_name} {col_type}"
                    ))
                    logger.info(f"Migration: added {col_name} to live_trades")
            await _mig_db.commit()
    except Exception as _mig_err:
        logger.warning(f"LiveTrade migration skipped (non-fatal): {_mig_err}")

    scheduler.start()
    logger.info("APScheduler started with market hour jobs")

    # Initialize broker session for the API container
    from app.ingestion.broker_manager import broker_manager
    await broker_manager.initialize_session()

    # ── Startup: reconcile PENDING live trades with broker (Audit Cat. 9) ──
    # Prevents stale PENDING trades from occupying position slots after a crash-restart.
    try:
        from app.core.database import async_session_factory as _asf
        from app.engine.execution_manager import ExecutionManager
        async with _asf() as _db:
            await ExecutionManager.reconcile_on_startup(_db)
        logger.info("Startup: live trade reconciliation complete")
    except Exception as _e:
        logger.warning(f"Startup: live trade reconciliation failed (non-fatal): {_e}")

    # ── Intelligence Pipeline + AutoTrader (ALWAYS runs) ─────────────────────
    # Corrective Refactor: Pipeline runs regardless of WORKER_MODE so that
    # all signals (real-time and scanner) follow the unified pipeline.

    # Start AutoTrader Queue worker
    try:
        from app.engine.auto_trader_engine import autotrader_queue
        autotrader_queue.start_worker()
        logger.info("Startup: AutoTrader Queue worker started")
    except Exception as _e:
        logger.warning(f"Startup: AutoTrader Queue worker failed (non-fatal): {_e}")

    # Wire Intelligence Pipeline callbacks
    # Architecture Audit: Added MetaStrategy, MarketRegimeFilter, LiquidityFilter
    # Pipeline: SignalPool → Consolidation → MetaStrategy → Confirmation →
    #           Quality → MarketRegime → ML → NLP → Time → Liquidity → FinalAlert
    try:
        from app.engine.signal_pool import signal_pool
        from app.engine.consolidation_layer import consolidation_layer
        from app.engine.meta_strategy_engine import meta_strategy_engine
        from app.engine.confirmation_layer import confirmation_layer
        from app.engine.quality_score_layer import quality_score_layer
        from app.engine.market_regime_filter import market_regime_filter
        from app.engine.ml_filter_layer import ml_filter_layer
        from app.engine.nlp_filter_layer import nlp_filter_layer
        from app.engine.time_filter_layer import time_filter_layer
        from app.engine.liquidity_filter_layer import liquidity_filter_layer
        from app.engine.final_alert_generator import final_alert_layer

        signal_pool.set_callback(consolidation_layer.process_signal_group)
        consolidation_layer.set_callback(meta_strategy_engine.filter_signal)
        meta_strategy_engine.set_callback(confirmation_layer.verify_confirmation)
        confirmation_layer.set_callback(quality_score_layer.score_signal)
        quality_score_layer.set_callback(market_regime_filter.evaluate)
        market_regime_filter.set_callback(ml_filter_layer.evaluate)
        ml_filter_layer.set_callback(nlp_filter_layer.evaluate)
        nlp_filter_layer.set_callback(time_filter_layer.evaluate)
        time_filter_layer.set_callback(liquidity_filter_layer.evaluate)
        liquidity_filter_layer.set_callback(final_alert_layer.process_alert)
        signal_pool.start()
        logger.info(
            "Startup: Intelligence Pipeline wired and started "
            "(11 layers: Pool → Consolidation → MetaStrategy → Confirmation → "
            "Quality → Regime → ML → NLP → Time → Liquidity → FinalAlert)"
        )
    except Exception as _e:
        logger.warning(f"Startup: Intelligence Pipeline failed (non-fatal): {_e}")

    # Load strategies into CandleConsumer StrategyRunner
    try:
        from app.engine.candle_consumer import candle_consumer
        await candle_consumer.load_strategies()
        logger.info("Startup: CandleConsumer strategies loaded from DB")
    except Exception as _e:
        logger.warning(f"Startup: CandleConsumer strategy loading failed (non-fatal): {_e}")

    # ── Worker Mode Detection ─────────────────────────────────────────────────
    import os
    _worker_mode = os.environ.get("WORKER_MODE", "distributed")

    if _worker_mode == "monolith":
        logger.info("WORKER_MODE=monolith — starting in-process Redis consumer")
        # Start Redis Stream consumer for live candles (monolith only;
        # in distributed mode, CandleConsumer runs as a separate service)
        try:
            from app.engine.candle_consumer import candle_consumer
            candle_consumer.start()
            logger.info("Startup: Redis candle consumer started (monolith)")
        except Exception as _e:
            logger.warning(f"Startup: Redis candle consumer failed (non-fatal): {_e}")

        # ── Fix 5: Candle Persister — persist candles to PostgreSQL ──────────
        try:
            from app.engine.candle_persister import candle_persister
            await candle_persister.start()
            logger.info("Startup: Candle persister started (monolith)")
        except Exception as _e:
            logger.warning(f"Startup: Candle persister failed (non-fatal): {_e}")
    else:
        logger.info(
            "WORKER_MODE=distributed — workers run as separate services "
            "(signal-engine, risk-engine, autotrader, trade-monitor)"
        )

    yield
    # Shutdown
    # Stop CandidateSignalPool background task
    try:
        from app.engine.signal_pool import signal_pool
        signal_pool.stop()
    except Exception as _e:
        pass

    # Fix 5: Stop candle persister
    try:
        from app.engine.candle_persister import candle_persister
        await candle_persister.stop()
    except Exception as _e:
        pass

    # Fix 13: Cancel pending orders on system shutdown
    try:
        from app.core.database import async_session_factory as _asf
        from app.engine.execution_manager import ExecutionManager
        async with _asf() as _db:
            mgr = ExecutionManager(_db)
            cancelled = await mgr.cancel_stale_pending_orders(timeout_minutes=0)
            logger.info(f"Shutdown: Cancelled {cancelled} pending orders")
    except Exception as _e:
        logger.warning(f"Shutdown: Failed to cancel pending orders (non-fatal): {_e}")
        
    scheduler.shutdown()
    from app.core.worker_pool import shutdown_worker_pool
    shutdown_worker_pool()
    logger.info("QuantDSS shut down gracefully")


from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from app.core.rate_limit import limiter

app = FastAPI(
    title="QuantDSS API",
    description="Personal Quant Trading Decision Support System — Advisory Only",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS middleware
origins = [origin.strip() for origin in settings.allowed_origins.split(",") if origin.strip() and origin.strip() != "*"]
if not origins:
    origins = ["http://localhost:3000"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount routers under /api/v1
app.include_router(auth.router, prefix="/api/v1", tags=["Auth"])
app.include_router(health.router, prefix="/api/v1", tags=["Health"])
app.include_router(symbols.router, prefix="/api/v1", tags=["Symbols"])
app.include_router(candles.router, prefix="/api/v1", tags=["Market Data"])
app.include_router(strategies.router, prefix="/api/v1", tags=["Strategies"])
app.include_router(risk.router, prefix="/api/v1", tags=["Risk"])
app.include_router(signals.router, prefix="/api/v1", tags=["Signals"])
app.include_router(trades.router, prefix="/api/v1", tags=["Trade Journal"])
app.include_router(backtest.router, prefix="/api/v1", tags=["Backtesting"])
app.include_router(performance.router, prefix="/api/v1", tags=["Performance"])
app.include_router(stream.router, prefix="/api/v1", tags=["Real-Time Stream"])
app.include_router(market_data.router, prefix="/api/v1", tags=["Market Data Seed"])
app.include_router(scanner.router, prefix="/api/v1", tags=["Signal Scanner"])
app.include_router(paper.router, prefix="/api/v1/paper", tags=["Paper Trading"])
app.include_router(auto_trader.router, prefix="/api/v1", tags=["Auto Trader"])
app.include_router(admin.router, prefix="/api/v1", tags=["Admin"])
app.include_router(replay.router, prefix="/api/v1/replay", tags=["Market Replay Engine"])
