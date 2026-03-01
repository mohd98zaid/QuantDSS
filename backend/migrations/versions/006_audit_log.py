"""006 — Audit log (immutable)

Revision ID: 006
Revises: 005
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "006"
down_revision: str | None = "005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("entity_type", sa.String(50), nullable=True),
        sa.Column("entity_id", sa.Integer(), nullable=True),
        sa.Column("payload", JSONB(), nullable=True),
        sa.Column("source", sa.String(50), server_default="system"),
    )
    op.create_index("idx_audit_timestamp", "audit_log", [sa.text("timestamp DESC")])
    op.create_index("idx_audit_event", "audit_log", ["event_type", sa.text("timestamp DESC")])


def downgrade() -> None:
    op.drop_table("audit_log")
