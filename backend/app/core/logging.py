"""
QuantDSS Logging — Loguru Structured JSON Logger
"""
import sys

from loguru import logger

from app.core.config import settings

# Remove default handler
logger.remove()

# Console output (development)
logger.add(
    sys.stdout,
    format="{time:YYYY-MM-DD HH:mm:ss} | {level:<8} | {name}:{line} | {message}",
    level="DEBUG" if settings.app_env == "development" else "INFO",
    colorize=True,
)

# File output (structured JSON — daily rotation)
logger.add(
    "logs/quantdss_{time:YYYY-MM-DD}.log",
    format="{time} | {level} | {name}:{line} | {message}",
    rotation="00:00",       # New file at midnight
    retention="30 days",    # Keep 30 days of logs
    serialize=True,         # JSON format for structured logging
    level="INFO",
    enqueue=True,           # Thread-safe
)

# Re-export for use throughout the application
__all__ = ["logger"]
