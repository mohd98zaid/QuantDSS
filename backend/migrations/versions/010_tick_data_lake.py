"""tick data lake

Revision ID: 010_tick_data_lake
Revises: 009_timescale_hardening
Create Date: 2026-03-08 21:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '010_tick_data_lake'
down_revision = '009_timescale_hardening'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Create ticks table
    op.create_table(
        'ticks',
        sa.Column('symbol', sa.String(), nullable=False),
        sa.Column('price', sa.Float(precision=53), nullable=False),
        sa.Column('volume', sa.BigInteger(), nullable=False),
        sa.Column('exchange_timestamp', sa.DateTime(timezone=True), nullable=False),
        sa.Column('ingestion_timestamp', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('sequence_id', sa.BigInteger(), autoincrement=True, nullable=False),
    )
    # Using execute since composite PK via op.create_table limits timescaledb unique constraints sometimes
    
    # 2. Create hypertable
    op.execute("SELECT create_hypertable('ticks', 'exchange_timestamp', if_not_exists => TRUE);")

    # 3. Add retention policy
    op.execute("SELECT add_retention_policy('ticks', INTERVAL '3 days', if_not_exists => TRUE);")

    # 4. Add compression policy
    op.execute("ALTER TABLE ticks SET (timescaledb.compress, timescaledb.compress_segmentby = 'symbol');")
    op.execute("SELECT add_compression_policy('ticks', INTERVAL '1 day', if_not_exists => TRUE);")


def downgrade() -> None:
    op.execute("SELECT remove_compression_policy('ticks', if_exists => TRUE);")
    op.execute("SELECT remove_retention_policy('ticks', if_exists => TRUE);")
    op.drop_table('ticks')
