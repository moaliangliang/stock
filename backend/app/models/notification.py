"""
通知模型 - 用户通知
"""
from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, JSON, ForeignKey, Index

from app.core.database import Base


class Notification(Base):
    """用户通知表"""
    __tablename__ = "notifications"
    __table_args__ = (
        Index("idx_notification_user_created", "user_id", "created_at"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, comment="用户ID")
    type = Column(String(50), nullable=False, comment="通知类型: trade/risk/strategy/system")
    title = Column(String(200), nullable=False, comment="通知标题")
    content = Column(Text, nullable=True, comment="通知内容")
    is_read = Column(Boolean, default=False, comment="是否已读")
    metadata_json = Column(JSON, default=dict, comment="附加数据")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
