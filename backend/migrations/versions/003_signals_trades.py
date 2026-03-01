"""003 — Signals, trades, daily_risk_state

Revision ID: 003
Revises: 002
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "003"
down_revision: str | None = "002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # signals
    op.create_table(
        "signals",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("strategy_id", sa.Integer(), sa.ForeignKey("strategies.id"), nullable=True),
        sa.Column("symbol_id", sa.Integer(), sa.ForeignKey("symbols.id"), nullable=True),
        sa.Column("signal_type", sa.String(10), nullable=False),
        sa.Column("entry_price", sa.Numeric(12, 2)),
        sa.Column("stop_loss", sa.Numeric(12, 2)),
        sa.Column("target_price", sa.Numeric(12, 2)),
        sa.Column("quantity", sa.Integer()),
        sa.Column("risk_amount", sa.Numeric(12, 2)),
        sa.Column("risk_pct", sa.Numeric(6, 4)),
        sa.Column("risk_reward", sa.Numeric(6, 2)),
        sa.Column("risk_status", sa.String(20), nullable=False),
        sa.Column("block_reason", sa.Text(), nullable=True),
        sa.Column("atr_value", sa.Numeric(12, 4)),
        sa.Column("atr_pct", sa.Numeric(6, 4)),
        sa.Column("candle_time", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("idx_signals_timestamp", "signals", [sa.text("timestamp DESC")])
    op.create_index("idx_signals_symbol", "signals", ["symbol_id", sa.text("timestamp DESC")])
    op.create_index("idx_signals_status", "signals", ["risk_status", sa.text("timestamp DESC")])

    # trades
    op.create_table(
        "trades",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("signal_id", sa.Integer(), sa.ForeignKey("signals.id"), nullable=True),
        sa.Column("symbol_id", sa.Integer(), sa.ForeignKey("symbols.id"), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("entry_time", sa.DateTime(timezone=True)),
        sa.Column("exit_time", sa.DateTime(timezone=True)),
        sa.Column("entry_price", sa.Numeric(12, 2)),
        sa.Column("exit_price", sa.Numeric(12, 2)),
        sa.Column("quantity", sa.Integer()),
        sa.Column("direction", sa.String(5), nullable=False),
        sa.Column("gross_pnl", sa.Numeric(12, 2)),
        sa.Column("brokerage", sa.Numeric(10, 2)),
        sa.Column("net_pnl", sa.Numeric(12, 2)),
        sa.Column("exit_reason", sa.String(50)),
        sa.Column("notes", sa.Text()),
        sa.Column("is_deleted", sa.Boolean(), server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("idx_trades_date", "trades", [sa.text("trade_date DESC")])
    op.create_index("idx_trades_symbol", "trades", ["symbol_id", sa.text("trade_date DESC")])

    # daily_risk_state
    op.create_table(
        "daily_risk_state",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("trade_date", sa.Date(), unique=True, nullable=False),
        sa.Column("account_balance", sa.Numeric(14, 2)),
        sa.Column("peak_balance", sa.Numeric(14, 2)),
        sa.Column("max_daily_loss", sa.Numeric(12, 2)),
        sa.Column("realised_pnl", sa.Numeric(12, 2), server_default="0"),
        sa.Column("unrealised_pnl", sa.Numeric(12, 2), server_default="0"),
        sa.Column("trades_taken", sa.Integer(), server_default="0"),
        sa.Column("signals_approved", sa.Integer(), server_default="0"),
        sa.Column("signals_blocked", sa.Integer(), server_default="0"),
        sa.Column("signals_skipped", sa.Integer(), server_default="0"),
        sa.Column("last_signal_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_halted", sa.Boolean(), server_default="false"),
        sa.Column("halt_reason", sa.Text(), nullable=True),
        sa.Column("halt_triggered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("daily_risk_state")
    op.drop_table("trades")
    op.drop_table("signals")
