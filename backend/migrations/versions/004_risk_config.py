"""004 — Risk config singleton

Revision ID: 004
Revises: 003
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "004"
down_revision: str | None = "003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "risk_config",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("risk_per_trade_pct", sa.Numeric(6, 4), server_default="0.01"),
        sa.Column("max_daily_loss_inr", sa.Numeric(12, 2), server_default="500.00"),
        sa.Column("max_daily_loss_pct", sa.Numeric(6, 4), server_default="0.02"),
        sa.Column("max_account_drawdown_pct", sa.Numeric(6, 4), server_default="0.10"),
        sa.Column("cooldown_minutes", sa.Integer(), server_default="15"),
        sa.Column("min_atr_pct", sa.Numeric(6, 4), server_default="0.003"),
        sa.Column("max_atr_pct", sa.Numeric(6, 4), server_default="0.030"),
        sa.Column("max_position_pct", sa.Numeric(6, 4), server_default="0.20"),
        sa.Column("max_concurrent_positions", sa.Integer(), server_default="2"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    # Enforce single row
    op.execute("CREATE UNIQUE INDEX idx_risk_config_singleton ON risk_config ((TRUE))")


def downgrade() -> None:
    op.drop_table("risk_config")
