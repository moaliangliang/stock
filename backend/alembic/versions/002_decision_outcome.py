"""Add decision_outcomes table for tracking investment decision accuracy.

Revision ID: 002
Revises: 001
Create Date: 2026-05-08
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "decision_outcomes",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("decision_id", sa.Integer, sa.ForeignKey("investment_decisions.id"), unique=True, nullable=False),
        sa.Column("symbol", sa.String(20), nullable=False),
        sa.Column("recommendation", sa.String(20), nullable=False),
        sa.Column("confidence", sa.Integer, default=50),
        sa.Column("entry_price", sa.Float, nullable=True),
        sa.Column("actual_high_24h", sa.Float, nullable=True),
        sa.Column("actual_low_24h", sa.Float, nullable=True),
        sa.Column("actual_close_24h", sa.Float, nullable=True),
        sa.Column("hit_target", sa.Boolean, default=False),
        sa.Column("hit_stop", sa.Boolean, default=False),
        sa.Column("pnl_pct", sa.Float, nullable=True),
        sa.Column("outcome", sa.String(20), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("decision_outcomes")
