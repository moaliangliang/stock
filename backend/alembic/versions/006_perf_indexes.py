"""Add performance indexes: InvestmentDecision(user_id), RiskRecord(symbol), Order(updated_at).

Revision ID: 006
Revises: 005
Create Date: 2026-05-12
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # InvestmentDecision: user_id filter + (user_id, status) composite for get_decisions()
    op.create_index("idx_investment_decisions_user_id", "investment_decisions", ["user_id"])
    op.create_index("idx_investment_decisions_user_status", "investment_decisions", ["user_id", "status"])

    # RiskRecord: symbol filter in risk scoring queries
    op.create_index("idx_risk_records_symbol", "risk_records", ["symbol"])

    # Order: updated_at filter in check_daily_loss (today's filled sell orders)
    op.create_index("idx_orders_updated_at", "orders", ["updated_at"])


def downgrade() -> None:
    op.drop_index("idx_orders_updated_at", table_name="orders")
    op.drop_index("idx_risk_records_symbol", table_name="risk_records")
    op.drop_index("idx_investment_decisions_user_status", table_name="investment_decisions")
    op.drop_index("idx_investment_decisions_user_id", table_name="investment_decisions")
