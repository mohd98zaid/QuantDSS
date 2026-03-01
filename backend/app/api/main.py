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
    performance,
    risk,
    signals,
    strategies,
    stream,
    symbols,
    trades,
)
from app.core.config import settings
from app.core.logging import logger
from app.core.scheduler import scheduler, setup_scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown events."""
    # Startup
    logger.info("QuantDSS starting up...")
    setup_scheduler()
    scheduler.start()
    logger.info("APScheduler started with market hour jobs")
    
    # Initialize broker session for the API container
    from app.ingestion.broker_manager import broker_manager
    await broker_manager.initialize_session()
    
    yield
    # Shutdown
    scheduler.shutdown()
    logger.info("QuantDSS shut down gracefully")


app = FastAPI(
    title="QuantDSS API",
    description="Personal Quant Trading Decision Support System — Advisory Only",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# CORS middleware
origins = [origin.strip() for origin in settings.allowed_origins.split(",")]
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
