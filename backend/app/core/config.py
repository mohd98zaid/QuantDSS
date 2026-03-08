"""
QuantDSS Configuration — Pydantic BaseSettings
Reads all configuration from environment variables / .env file.

Critical Fix 4 (Audit): Removed insecure default credentials.
SECRET_KEY, DEFAULT_USERNAME, and DEFAULT_PASSWORD must be set in .env.
"""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # ─── Application ────────────────────────────────────────
    app_env: str = "development"
    secret_key: str = ""  # REQUIRED — must be set in .env
    allowed_origins: str = "http://localhost:3000,http://localhost"
    log_level: str = "INFO"

    # ─── Database ───────────────────────────────────────────
    database_url: str = "postgresql+asyncpg://quant:password@postgres:5432/quantdb"

    # ─── Redis ──────────────────────────────────────────────
    redis_url: str = "redis://:password@redis:6379/0"

    # ─── Broker: Shoonya (Finvasia) ───────────────────────────
    shoonya_user_id: str | None = None
    shoonya_password: str | None = None
    shoonya_totp_secret: str | None = None
    shoonya_api_key: str | None = None
    shoonya_vendor_code: str | None = None
    shoonya_imei: str = "noimeiespecified"

    # ─── Broker: Angel One SmartAPI — FALLBACK ──────────────
    angel_api_key: str | None = None
    angel_api_secret: str | None = None
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

    # ─── Trading Mode ────────────────────────────────────────
    # Controls default execution mode: "disabled", "paper", or "live".
    # The active mode is always read from AutoTradeConfig.mode (DB) at runtime;
    # this setting only determines the default seeded into the DB on first run.
    trading_mode: str = "paper"

    # Safety lock for LIVE mode — must be set in .env (never in code).
    # TradingModeController reads this directly from os.environ.
    # See app/engine/trading_mode.py for enforcement logic.
    live_trading_lock: str = ""  # Set to "CONFIRMED" in .env to enable LIVE mode.

    # ─── Signal Worker Sharding ──────────────────────────────
    signal_worker_id: int = 0
    signal_worker_total: int = 1

    # ─── Kafka ───────────────────────────────────────────────
    kafka_bootstrap_servers: str = "kafka:9092"
    kafka_enabled: bool = False

    # ─── AngelOne (Dual Feed) ────────────────────────────────────────
    angel_api_key: str = ""
    angel_client_id: str = ""
    angel_password: str = ""
    angel_totp_secret: str = ""

    # ─── Default User (single-user system) ───────────────────
    # REQUIRED — must be set in .env as DEFAULT_USERNAME / DEFAULT_PASSWORD.
    default_username: str = ""
    default_password: str = ""

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
        "extra": "ignore",
    }

    def validate_security(self) -> None:
        """
        Critical Fix 4 (Audit): Validate that security-critical settings
        are configured. Called at application startup.
        Raises RuntimeError if any required setting is missing.
        """
        errors = []
        if not self.secret_key or self.secret_key == "change-this-to-a-random-64-char-string":
            errors.append("SECRET_KEY must be set in .env (use a random 64+ char string)")
        if not self.default_username:
            errors.append("DEFAULT_USERNAME must be set in .env")
        if not self.default_password:
            errors.append("DEFAULT_PASSWORD must be set in .env")
        if errors:
            raise RuntimeError(
                "Security validation failed. Fix these issues in your .env file:\n  - "
                + "\n  - ".join(errors)
            )


# Singleton settings instance
settings = Settings()
