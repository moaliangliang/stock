"""
持仓管理模型
"""
from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship

from app.core.database import Base


class Position(Base):
    """持仓表"""
    __tablename__ = "positions"
    __table_args__ = (
        UniqueConstraint("user_id", "symbol", name="uq_user_position"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, comment="用户ID")
    symbol = Column(String(20), nullable=False, comment="标的代码")
    quantity = Column(Float, default=0, comment="持仓数量")
    available_quantity = Column(Float, default=0, comment="可用数量")
    frozen_quantity = Column(Float, default=0, comment="冻结数量")
    cost_price = Column(Float, default=0, comment="成本价")
    current_price = Column(Float, default=0, comment="当前价")
    market_value = Column(Float, default=0, comment="市值")
    pnl = Column(Float, default=0, comment="盈亏")
    pnl_ratio = Column(Float, default=0, comment="盈亏比例(%)")
    day_pnl = Column(Float, default=0, comment="当日盈亏")
    day_pnl_ratio = Column(Float, default=0, comment="当日盈亏比例(%)")
    margin = Column(Float, default=0, comment="占用保证金")
    leverage = Column(Float, default=1, comment="杠杆倍数")
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    user = relationship("User", back_populates="positions")
