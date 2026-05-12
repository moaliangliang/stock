"""Add missing FKs (trade_logs, decision_outcomes) and indexes (is_watched, symbol).

Revision ID: 005
Revises: 004
Create Date: 2026-05-12
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect != "postgresql":
        return  # SQLite handles FKs/indexes via create_all

    # --- FKs ---
    # trade_logs FKs (added after initial schema)
    for col, ref_table, ondelete in [
        ("user_id", "users", "CASCADE"),
        ("order_id", "orders", "SET NULL"),
        ("strategy_id", "strategies", "SET NULL"),
    ]:
        constraint = f"trade_logs_{col}_fkey"
        op.execute(f'ALTER TABLE trade_logs DROP CONSTRAINT IF EXISTS "{constraint}"')
        op.execute(
            f'ALTER TABLE trade_logs ADD CONSTRAINT "{constraint}" '
            f"FOREIGN KEY ({col}) REFERENCES {ref_table}(id) ON DELETE {ondelete}"
        )

    # decision_outcomes FK missing ondelete=CASCADE from migration 002
    op.execute(
        'ALTER TABLE decision_outcomes DROP CONSTRAINT IF EXISTS "decision_outcomes_decision_id_fkey"'
    )
    op.execute(
        'ALTER TABLE decision_outcomes ADD CONSTRAINT "decision_outcomes_decision_id_fkey" '
        "FOREIGN KEY (decision_id) REFERENCES investment_decisions(id) ON DELETE CASCADE"
    )

    # --- Indexes ---
    op.execute("CREATE INDEX IF NOT EXISTS idx_symbol_info_watched ON symbol_info (is_watched)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_investment_decisions_symbol ON investment_decisions (symbol)")


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect != "postgresql":
        return

    op.execute('ALTER TABLE decision_outcomes DROP CONSTRAINT IF EXISTS "decision_outcomes_decision_id_fkey"')
    op.execute(
        'ALTER TABLE decision_outcomes ADD CONSTRAINT "decision_outcomes_decision_id_fkey" '
        "FOREIGN KEY (decision_id) REFERENCES investment_decisions(id)"
    )
    for col, ref_table, _ in [
        ("user_id", "users", "CASCADE"),
        ("order_id", "orders", "SET NULL"),
        ("strategy_id", "strategies", "SET NULL"),
    ]:
        constraint = f"trade_logs_{col}_fkey"
        op.execute(f'ALTER TABLE trade_logs DROP CONSTRAINT IF EXISTS "{constraint}"')
        op.execute(
            f'ALTER TABLE trade_logs ADD CONSTRAINT "{constraint}" '
            f"FOREIGN KEY ({col}) REFERENCES {ref_table}(id)"
        )

    op.execute("DROP INDEX IF EXISTS idx_symbol_info_watched")
    op.execute("DROP INDEX IF EXISTS idx_investment_decisions_symbol")
