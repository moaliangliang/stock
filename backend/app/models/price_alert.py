"""
价格提醒模型 - 用户设定的价格阈值提醒
"""
from datetime import datetime, timezone

from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, UniqueConstraint, Index
from sqlalchemy.orm import relationship

from app.core.database import Base


class PriceAlert(Base):
    """价格提醒"""
    __tablename__ = "price_alerts"
    __table_args__ = (
        UniqueConstraint("user_id", "symbol", "condition", "target_price", name="uq_user_alert"),
        Index("idx_price_alerts_status", "status"),
        Index("idx_price_alerts_user_status", "user_id", "status"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    symbol = Column(String(50), nullable=False, comment="标的代码")
    condition = Column(String(10), nullable=False, comment="触发条件: above/below")
    target_price = Column(Float, nullable=False, comment="目标价格")
    status = Column(String(20), default="active", comment="状态: active/triggered/disabled")
    triggered_at = Column(DateTime(timezone=True), nullable=True, comment="触发时间")
    triggered_price = Column(Float, nullable=True, comment="触发时的价格")
    message = Column(String(200), nullable=True, comment="自定义提醒内容")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    user = relationship("User", back_populates="price_alerts")
