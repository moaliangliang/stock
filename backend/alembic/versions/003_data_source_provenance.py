"""Add data_source columns to kline_data and ticker_data

Revision ID: 003
Revises: 002
Create Date: 2026-05-09
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # KLine table
    op.add_column("kline_data", sa.Column(
        "data_source", sa.String(20), nullable=False, server_default="unknown",
        comment="数据来源: eastmoney/sina/akshare/mock/unknown"
    ))
    op.create_index("idx_kline_data_source", "kline_data", ["data_source"])

    # Ticker table
    op.add_column("ticker_data", sa.Column(
        "data_source", sa.String(20), nullable=False, server_default="unknown",
        comment="数据来源: eastmoney/sina/mock/unknown"
    ))
    op.create_index("idx_ticker_data_source", "ticker_data", ["data_source"])


def downgrade() -> None:
    op.drop_index("idx_ticker_data_source", table_name="ticker_data")
    op.drop_column("ticker_data", "data_source")
    op.drop_index("idx_kline_data_source", table_name="kline_data")
    op.drop_column("kline_data", "data_source")
