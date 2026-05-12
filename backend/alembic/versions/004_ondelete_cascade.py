"""Add ON DELETE CASCADE/SET NULL to foreign keys and sync missing indexes.

Revision ID: 004
Revises: 003
Create Date: 2026-05-12

Changes:
  - Recreate all FK constraints with ondelete (CASCADE for non-null, SET NULL for nullable)
  - Create any indexes defined in model __table_args__ that may be missing from DB
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# FK definitions: (table, constraint_name, column, ref_table, ondelete)
_FKS_TO_RECREATE = [
    # CASCADE — non-nullable user-owned data
    ("price_alerts", "price_alerts_user_id_fkey", "user_id", "users", "CASCADE"),
    ("api_keys", "api_keys_user_id_fkey", "user_id", "users", "CASCADE"),
    ("risk_records", "risk_records_user_id_fkey", "user_id", "users", "CASCADE"),
    ("orders", "orders_user_id_fkey", "user_id", "users", "CASCADE"),
    ("trades", "trades_order_id_fkey", "order_id", "orders", "CASCADE"),
    ("notifications", "notifications_user_id_fkey", "user_id", "users", "CASCADE"),
    ("investment_decisions", "investment_decisions_user_id_fkey", "user_id", "users", "CASCADE"),
    ("strategies", "strategies_user_id_fkey", "user_id", "users", "CASCADE"),
    ("strategy_run_logs", "strategy_run_logs_strategy_id_fkey", "strategy_id", "strategies", "CASCADE"),
    ("positions", "positions_user_id_fkey", "user_id", "users", "CASCADE"),
    # SET NULL — nullable references
    ("risk_rules", "risk_rules_user_id_fkey", "user_id", "users", "SET NULL"),
    ("risk_records", "risk_records_rule_id_fkey", "rule_id", "risk_rules", "SET NULL"),
    ("orders", "orders_strategy_id_fkey", "strategy_id", "strategies", "SET NULL"),
]

# Indexes defined in model __table_args__ that may not exist in DB
# (some were added after initial create_all)
_MISSING_INDEXES = [
    ("idx_kline_symbol_interval_ts", "kline_data", ["symbol", "interval", "timestamp"]),
    ("idx_risk_records_user_created", "risk_records", ["user_id", "created_at"]),
    ("idx_notification_user_created", "notifications", ["user_id", "created_at"]),
    ("idx_log_category_time", "system_logs", ["category", "created_at"]),
    ("idx_trade_log_time", "trade_logs", ["user_id", "created_at"]),
    ("idx_decision_outcomes_symbol", "decision_outcomes", ["symbol"]),
    ("idx_strategy_run_logs_sid_time", "strategy_run_logs", ["strategy_id", "run_time"]),
]


def upgrade() -> None:
    dialect = op.get_bind().dialect.name

    # FK constraints: PostgreSQL supports ALTER TABLE DROP/ADD CONSTRAINT.
    if dialect == "postgresql":
        for table, constraint, column, ref_table, ondelete in _FKS_TO_RECREATE:
            op.execute(
                f'ALTER TABLE "{table}" DROP CONSTRAINT IF EXISTS "{constraint}"'
            )
            op.execute(
                f'ALTER TABLE "{table}" ADD CONSTRAINT "{constraint}" '
                f"FOREIGN KEY ({column}) REFERENCES {ref_table}(id)"
                + (f" ON DELETE {ondelete}" if ondelete else "")
            )

    # Create missing indexes (safe with IF NOT EXISTS on PG, SQLite skips duplicates)
    if dialect == "postgresql":
        for idx_name, table, columns in _MISSING_INDEXES:
            col_expr = ", ".join(columns)
            op.execute(
                f"CREATE INDEX IF NOT EXISTS {idx_name} ON {table} ({col_expr})"
            )


def downgrade() -> None:
    dialect = op.get_bind().dialect.name

    if dialect == "postgresql":
        for table, constraint, column, ref_table, _ in _FKS_TO_RECREATE:
            op.execute(
                f'ALTER TABLE "{table}" DROP CONSTRAINT IF EXISTS "{constraint}"'
            )
            # Recreate without ondelete
            op.execute(
                f'ALTER TABLE "{table}" ADD CONSTRAINT "{constraint}" '
                f"FOREIGN KEY ({column}) REFERENCES {ref_table}(id)"
            )
