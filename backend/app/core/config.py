"""
QuantDSS Configuration — Pydantic BaseSettings
Reads all configuration from environment variables / .env file.
"""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # ─── Application ────────────────────────────────────────
    app_env: str = "development"
    secret_key: str = "change-this-to-a-random-64-char-string"
    allowed_origins: str = "http://localhost:3000,http://localhost"
    log_level: str = "INFO"

    # ─── Database ───────────────────────────────────────────
    database_url: str = "postgresql+asyncpg://quant:password@postgres:5432/quantdb"

    # ─── Redis ──────────────────────────────────────────────
    redis_url: str = "redis://:password@redis:6379/0"

    # ─── Broker: Shoonya (Finvasia) — PRIMARY ───────────────
    shoonya_user_id: str | None = None
    shoonya_password: str | None = None
    shoonya_totp_secret: str | None = None
    shoonya_api_key: str | None = None
    shoonya_vendor_code: str | None = None
    shoonya_imei: str = "noimeiespecified"

    # ─── Broker: Angel One SmartAPI — FALLBACK ──────────────
    angel_api_key: str | None = None
    angel_client_id: str | None = None
    angel_password: str | None = None
    angel_totp_secret: str | None = None

    # ─── Broker: Upstox ─────────────────────────────────────
    upstox_api_key: str | None = None
    upstox_api_secret: str | None = None
    upstox_redirect_uri: str | None = None
    upstox_access_token: str | None = None

    # ─── Telegram Bot ───────────────────────────────────────
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None

    # ─── Risk Defaults ──────────────────────────────────────
    primary_broker: str = "upstox"
    backup_broker: str = "angel"
    
    risk_per_trade_pct: float = 0.01
    max_daily_loss_inr: float = 500.0
    max_daily_loss_pct: float = 0.02
    max_account_drawdown_pct: float = 0.10
    cooldown_minutes: int = 15
    min_atr_pct: float = 0.003
    max_atr_pct: float = 0.030
    max_position_pct: float = 0.20
    max_concurrent_positions: int = 2

    # ─── JWT ────────────────────────────────────────────────
    jwt_algorithm: str = "HS256"
    jwt_expire_seconds: int = 86400  # 24 hours

    # ─── Default User (single-user system) ──────────────────
    default_username: str = "trader"
    default_password: str = "quantdss2025"

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
    }


# Singleton settings instance
settings = Settings()
