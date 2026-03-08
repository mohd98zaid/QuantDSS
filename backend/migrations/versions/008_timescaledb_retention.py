"""add timescaledb retention policy

Revision ID: 008
Revises: aeecb8c9bbea
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = '008'
down_revision: str | None = 'aeecb8c9bbea'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Add retention policy to drop data older than 30 days
    op.execute("SELECT add_retention_policy('candles', INTERVAL '30 days')")


def downgrade() -> None:
    # Remove retention policy
    op.execute("SELECT remove_retention_policy('candles', if_exists => true)")
