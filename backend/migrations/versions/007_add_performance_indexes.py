"""007 — Add performance indexes for signals and candles tables.

Revision ID: 007_performance_indexes
Revises: 006_audit_log
Create Date: 2026-03-06
"""
from alembic import op

revision = "007_performance_indexes"
down_revision = "006_audit_log"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Signals: fast lookups by timestamp
    op.create_index(
        "ix_signals_timestamp",
        "signals",
        ["timestamp"],
        if_not_exists=True,
    )
    # Signals: fast lookups by symbol + timestamp (composite)
    op.create_index(
        "ix_signals_symbol_id_timestamp",
        "signals",
        ["symbol_id", "timestamp"],
        if_not_exists=True,
    )
    # Candles: fast lookups by symbol + timestamp (composite)
    op.create_index(
        "ix_candles_symbol_id_timestamp",
        "candles",
        ["symbol_id", "timestamp"],
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index("ix_candles_symbol_id_timestamp", table_name="candles")
    op.drop_index("ix_signals_symbol_id_timestamp", table_name="signals")
    op.drop_index("ix_signals_timestamp", table_name="signals")
