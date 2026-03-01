"""005 — Backtest tables: backtest_runs, backtest_trades

Revision ID: 005
Revises: 004
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "005"
down_revision: str | None = "004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "backtest_runs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("strategy_id", sa.Integer(), sa.ForeignKey("strategies.id"), nullable=True),
        sa.Column("symbol_id", sa.Integer(), sa.ForeignKey("symbols.id"), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("initial_capital", sa.Numeric(14, 2)),
        sa.Column("slippage_pct", sa.Numeric(6, 4), server_default="0.0005"),
        sa.Column("brokerage_per_order", sa.Numeric(10, 2), server_default="20.00"),
        sa.Column("status", sa.String(20), server_default="PENDING"),
        sa.Column("total_return_pct", sa.Numeric(10, 4)),
        sa.Column("cagr_pct", sa.Numeric(10, 4)),
        sa.Column("win_rate_pct", sa.Numeric(6, 4)),
        sa.Column("profit_factor", sa.Numeric(8, 4)),
        sa.Column("sharpe_ratio", sa.Numeric(8, 4)),
        sa.Column("sortino_ratio", sa.Numeric(8, 4)),
        sa.Column("max_drawdown_pct", sa.Numeric(6, 4)),
        sa.Column("max_drawdown_days", sa.Integer()),
        sa.Column("total_trades", sa.Integer()),
        sa.Column("winning_trades", sa.Integer()),
        sa.Column("losing_trades", sa.Integer()),
        sa.Column("insample_end_date", sa.Date(), nullable=True),
        sa.Column("insample_return_pct", sa.Numeric(10, 4)),
        sa.Column("oos_return_pct", sa.Numeric(10, 4)),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "backtest_trades",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("backtest_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("symbol_id", sa.Integer(), sa.ForeignKey("symbols.id"), nullable=True),
        sa.Column("entry_time", sa.DateTime(timezone=True)),
        sa.Column("exit_time", sa.DateTime(timezone=True)),
        sa.Column("entry_price", sa.Numeric(12, 2)),
        sa.Column("exit_price", sa.Numeric(12, 2)),
        sa.Column("quantity", sa.Integer()),
        sa.Column("direction", sa.String(5)),
        sa.Column("gross_pnl", sa.Numeric(12, 2)),
        sa.Column("costs", sa.Numeric(10, 2)),
        sa.Column("net_pnl", sa.Numeric(12, 2)),
        sa.Column("exit_reason", sa.String(50)),
        sa.Column("risk_status", sa.String(20)),
        sa.Column("block_reason", sa.Text(), nullable=True),
    )
    op.create_index("idx_bt_trades_run", "backtest_trades", ["run_id"])


def downgrade() -> None:
    op.drop_table("backtest_trades")
    op.drop_table("backtest_runs")
