"""timescale hardening

Revision ID: 009
Revises: 3c46a7986c10
Create Date: 2026-03-08 19:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers
revision: str = '009'
down_revision: Union[str, None] = '3c46a7986c10'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 0. Adjust Primary Keys for TimescaleDB
    # Timescale requires the partitioning column to be part of the primary key
    op.execute("ALTER TABLE trades DROP CONSTRAINT IF EXISTS trades_signal_id_fkey;")
    op.execute("ALTER TABLE signals DROP CONSTRAINT signals_pkey;")
    op.execute("ALTER TABLE signals ADD PRIMARY KEY (id, created_at);")
    
    op.execute("ALTER TABLE order_event DROP CONSTRAINT order_event_pkey;")
    op.execute("ALTER TABLE order_event ADD PRIMARY KEY (id, event_timestamp);")

    # 1. Hypertable Conversion
    op.execute("SELECT create_hypertable('candles', 'time', if_not_exists => TRUE, migrate_data => TRUE);")
    op.execute("SELECT create_hypertable('signals', 'created_at', if_not_exists => TRUE, migrate_data => TRUE);")
    op.execute("SELECT create_hypertable('order_event', 'event_timestamp', if_not_exists => TRUE, migrate_data => TRUE);")

    # 2. Retention Policies
    op.execute("SELECT add_retention_policy('candles', INTERVAL '30 days', if_not_exists => TRUE);")
    op.execute("SELECT add_retention_policy('signals', INTERVAL '90 days', if_not_exists => TRUE);")
    op.execute("SELECT add_retention_policy('order_event', INTERVAL '180 days', if_not_exists => TRUE);")

    # 3. Compression Setup
    op.execute("ALTER TABLE candles SET (timescaledb.compress, timescaledb.compress_segmentby = 'symbol_id');")
    op.execute("SELECT add_compression_policy('candles', INTERVAL '7 days', if_not_exists => TRUE);")

    op.execute("ALTER TABLE signals SET (timescaledb.compress, timescaledb.compress_segmentby = 'symbol_id');")
    op.execute("SELECT add_compression_policy('signals', INTERVAL '7 days', if_not_exists => TRUE);")

    op.execute("ALTER TABLE order_event SET (timescaledb.compress, timescaledb.compress_segmentby = 'order_id');")
    op.execute("SELECT add_compression_policy('order_event', INTERVAL '7 days', if_not_exists => TRUE);")

    # 4. Index Optimization
    op.execute("CREATE INDEX IF NOT EXISTS idx_candles_symbol_time ON candles(symbol_id, time DESC);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_signals_symbol_time ON signals(symbol_id, created_at DESC);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_order_events_trade_time ON order_event(order_id, event_timestamp DESC);")


def downgrade() -> None:
    # 4. Remove Indexes
    op.execute("DROP INDEX IF EXISTS idx_candles_symbol_time;")
    op.execute("DROP INDEX IF EXISTS idx_signals_symbol_time;")
    op.execute("DROP INDEX IF EXISTS idx_order_events_trade_time;")

    # 3. Remove Compression Policies & Disable Compression
    op.execute("SELECT remove_compression_policy('candles', if_exists => TRUE);")
    op.execute("ALTER TABLE candles SET (timescaledb.compress = false);")
    
    op.execute("SELECT remove_compression_policy('signals', if_exists => TRUE);")
    op.execute("ALTER TABLE signals SET (timescaledb.compress = false);")
    
    op.execute("SELECT remove_compression_policy('order_event', if_exists => TRUE);")
    op.execute("ALTER TABLE order_event SET (timescaledb.compress = false);")

    # 2. Remove Retention Policies
    op.execute("SELECT remove_retention_policy('candles', if_exists => TRUE);")
    op.execute("SELECT remove_retention_policy('signals', if_exists => TRUE);")
    op.execute("SELECT remove_retention_policy('order_event', if_exists => TRUE);")

    # Note: Downgrading a hypertable back to a regular table is not natively supported by a simple command
    # in TimescaleDB without moving data. We leave it as a hypertable during downgrade or require manual intervention.
