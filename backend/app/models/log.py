"""
日志监控模型 - 系统日志、交易日志
"""
from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Float, Text, DateTime, ForeignKey, Enum as SAEnum, Index
import enum

from app.core.database import Base


class LogLevel(str, enum.Enum):
    """日志级别"""
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class LogCategory(str, enum.Enum):
    """日志分类"""
    SYSTEM = "system"           # 系统运行
    TRADE = "trade"             # 交易日志
    STRATEGY = "strategy"       # 策略运行
    MARKET = "market"           # 行情数据
    BACKTEST = "backtest"       # 回测日志
    RISK = "risk"               # 风控日志
    USER = "user"               # 用户操作


class SystemLog(Base):
    """系统日志表"""
    __tablename__ = "system_logs"
    __table_args__ = (
        Index("idx_log_category_time", "category", "created_at"),
    )

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    category = Column(SAEnum(LogCategory), nullable=False, comment="日志分类")
    level = Column(SAEnum(LogLevel), default=LogLevel.INFO, comment="日志级别")
    title = Column(String(200), nullable=False, comment="日志标题")
    content = Column(Text, nullable=True, comment="日志详情")
    user_id = Column(Integer, nullable=True, comment="关联用户ID(可选)")
    strategy_id = Column(Integer, nullable=True, comment="关联策略ID(可选)")
    source = Column(String(50), nullable=True, comment="来源组件")
    ip_address = Column(String(50), nullable=True, comment="来源IP")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), comment="创建时间")


class TradeLog(Base):
    """交易日志表 - 记录每笔交易操作"""
    __tablename__ = "trade_logs"
    __table_args__ = (
        Index("idx_trade_log_time", "user_id", "created_at"),
    )

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, comment="用户ID")
    order_id = Column(Integer, ForeignKey("orders.id", ondelete="SET NULL"), nullable=True, comment="关联订单ID")
    strategy_id = Column(Integer, ForeignKey("strategies.id", ondelete="SET NULL"), nullable=True, comment="关联策略ID")
    symbol = Column(String(20), nullable=False, comment="标的代码")
    action = Column(String(20), nullable=False, comment="操作: open/close/partial/cancel")
    side = Column(String(10), nullable=False, comment="方向: buy/sell")
    price = Column(Float, nullable=True, comment="价格")
    quantity = Column(Float, nullable=True, comment="数量")
    amount = Column(Float, nullable=True, comment="金额")
    status = Column(String(20), nullable=False, comment="状态")
    message = Column(Text, nullable=True, comment="备注")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
