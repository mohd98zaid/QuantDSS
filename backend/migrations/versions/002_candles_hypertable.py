"""002 — Candles hypertable with TimescaleDB

Revision ID: 002
Revises: 001
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "002"
down_revision: str | None = "001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Create candles table
    op.create_table(
        "candles",
        sa.Column("time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("symbol_id", sa.Integer(), sa.ForeignKey("symbols.id"), nullable=False),
        sa.Column("timeframe", sa.String(10), nullable=False, server_default="1min"),
        sa.Column("open", sa.Numeric(12, 2)),
        sa.Column("high", sa.Numeric(12, 2)),
        sa.Column("low", sa.Numeric(12, 2)),
        sa.Column("close", sa.Numeric(12, 2)),
        sa.Column("volume", sa.BigInteger()),
        sa.PrimaryKeyConstraint("time", "symbol_id", "timeframe"),
    )

    # Convert to TimescaleDB hypertable
    op.execute("SELECT create_hypertable('candles', 'time', migrate_data => true)")

    # Set chunk interval to 1 day for 1-min data
    op.execute("SELECT set_chunk_time_interval('candles', INTERVAL '1 day')")

    # Critical index for strategy lookback queries
    op.create_index("idx_candles_symbol_time", "candles", ["symbol_id", sa.text("time DESC")])

    # Enable compression for candles older than 7 days
    op.execute("""
        ALTER TABLE candles SET (
            timescaledb.compress,
            timescaledb.compress_segmentby = 'symbol_id, timeframe'
        )
    """)
    op.execute("SELECT add_compression_policy('candles', INTERVAL '7 days')")


def downgrade() -> None:
    # Remove compression policy first
    op.execute("SELECT remove_compression_policy('candles', if_exists => true)")
    op.drop_table("candles")
