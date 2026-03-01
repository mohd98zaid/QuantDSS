"""001 — Initial schema: symbols, strategies, strategy_symbols

Revision ID: 001
Revises: None
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # symbols
    op.create_table(
        "symbols",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("trading_symbol", sa.String(50), nullable=False, unique=True),
        sa.Column("exchange", sa.String(10), nullable=False),
        sa.Column("instrument_token", sa.BigInteger(), nullable=True),
        sa.Column("lot_size", sa.Integer(), server_default="1"),
        sa.Column("tick_size", sa.Numeric(10, 4), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("idx_symbols_active", "symbols", ["is_active"])

    # strategies
    op.create_table(
        "strategies",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("type", sa.String(50), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("parameters", JSONB(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true"),
        sa.Column("min_backtest_trades", sa.Integer(), server_default="30"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # strategy_symbols
    op.create_table(
        "strategy_symbols",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("strategy_id", sa.Integer(), sa.ForeignKey("strategies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("symbol_id", sa.Integer(), sa.ForeignKey("symbols.id", ondelete="CASCADE"), nullable=False),
        sa.Column("timeframe", sa.String(10), server_default="1min"),
        sa.Column("is_active", sa.Boolean(), server_default="true"),
        sa.UniqueConstraint("strategy_id", "symbol_id", name="uq_strategy_symbol"),
    )
    op.create_index("idx_ss_strategy", "strategy_symbols", ["strategy_id", "is_active"])


def downgrade() -> None:
    op.drop_table("strategy_symbols")
    op.drop_table("strategies")
    op.drop_table("symbols")
