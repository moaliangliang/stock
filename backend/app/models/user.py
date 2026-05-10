"""
用户权限模块模型 - 用户、角色、API密钥
"""
from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text, Enum as SAEnum
from sqlalchemy.orm import relationship
import enum

from app.core.database import Base


class UserRole(str, enum.Enum):
    """用户角色"""
    ADMIN = "admin"        # 管理员
    TRADER = "trader"      # 交易员
    ANALYST = "analyst"    # 分析师
    VIEWER = "viewer"      # 只读用户


class User(Base):
    """用户表"""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, index=True, nullable=False, comment="用户名")
    email = Column(String(100), unique=True, index=True, nullable=False, comment="邮箱")
    hashed_password = Column(String(200), nullable=False, comment="密码哈希")
    nickname = Column(String(50), nullable=True, comment="昵称")
    role = Column(SAEnum(UserRole), default=UserRole.VIEWER, comment="角色")
    is_active = Column(Boolean, default=True, comment="是否激活")
    is_superuser = Column(Boolean, default=False, comment="是否超级用户")
    max_position_ratio = Column(Integer, default=30, comment="最大仓位比例(%)")
    max_daily_loss = Column(Integer, default=5, comment="最大日亏损比例(%)")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), comment="创建时间")
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), comment="更新时间")

    # 关联
    api_keys = relationship("APIKey", back_populates="user", cascade="all, delete-orphan")
    strategies = relationship("Strategy", back_populates="user", cascade="all, delete-orphan")
    positions = relationship("Position", back_populates="user", cascade="all, delete-orphan")
    orders = relationship("Order", back_populates="user", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<User(id={self.id}, username={self.username}, role={self.role})>"


class APIKey(Base):
    """API密钥表 - 用于程序化交易接入"""
    __tablename__ = "api_keys"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, comment="用户ID")
    exchange = Column(String(50), nullable=False, comment="交易所")
    api_key = Column(String(200), nullable=False, comment="API Key")
    secret_key = Column(String(500), nullable=False, comment="Secret Key")
    passphrase = Column(String(200), nullable=True, comment="密码短语(部分交易所需要)")
    is_active = Column(Boolean, default=True, comment="是否激活")
    remark = Column(String(200), nullable=True, comment="备注")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), comment="创建时间")
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), comment="更新时间")

    user = relationship("User", back_populates="api_keys")
