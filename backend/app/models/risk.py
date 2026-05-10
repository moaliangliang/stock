"""
风控系统模型 - 风控规则、风控记录
"""
from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, ForeignKey, Text, Enum as SAEnum, Index
import enum

from app.core.database import Base


class RiskRuleType(str, enum.Enum):
    """风控规则类型"""
    MAX_DAILY_LOSS = "max_daily_loss"           # 单日最大亏损
    MAX_POSITION_RATIO = "max_position_ratio"   # 最大仓位比例
    MAX_POSITION_QTY = "max_position_qty"       # 最大持仓数量
    STOP_LOSS = "stop_loss"                     # 单笔止损
    BLACKLIST = "blacklist"                     # 黑名单
    MAX_ORDER_COUNT = "max_order_count"         # 单日最大下单次数
    MAX_OPEN_ORDERS = "max_open_orders"         # 最大挂单数量


class RiskAction(str, enum.Enum):
    """触发动作"""
    WARN = "warn"           # 仅警告
    BLOCK = "block"         # 阻止交易
    CLOSE = "close"         # 强制平仓


class RiskRule(Base):
    """风控规则表"""
    __tablename__ = "risk_rules"
    __table_args__ = (
        Index("idx_risk_rules_type_active", "rule_type", "is_active"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, comment="用户ID(null=全局规则)")
    name = Column(String(100), nullable=False, comment="规则名称")
    rule_type = Column(SAEnum(RiskRuleType), nullable=False, comment="规则类型")
    action = Column(SAEnum(RiskAction), default=RiskAction.WARN, comment="触发动作")
    is_active = Column(Boolean, default=True, comment="是否启用")

    # 规则参数 (JSON)
    params = Column(Text, nullable=True, comment="规则参数(JSON格式)")

    # 适用标的（逗号分隔，空=全部）
    symbols = Column(String(500), nullable=True, comment="适用标的")

    description = Column(Text, nullable=True, comment="规则描述")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class RiskRecord(Base):
    """风控触发记录表"""
    __tablename__ = "risk_records"
    __table_args__ = (
        Index("idx_risk_records_user_created", "user_id", "created_at"),
    )

    id = Column(Integer, primary_key=True, index=True)
    rule_id = Column(Integer, ForeignKey("risk_rules.id"), nullable=True, comment="风控规则ID")
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, comment="用户ID")
    symbol = Column(String(20), nullable=True, comment="关联标的")
    action = Column(SAEnum(RiskAction), nullable=False, comment="触发动作")
    trigger_value = Column(Float, nullable=True, comment="触发值")
    limit_value = Column(Float, nullable=True, comment="限制值")
    message = Column(Text, nullable=True, comment="详细信息")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
