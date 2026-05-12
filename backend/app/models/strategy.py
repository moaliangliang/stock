"""
策略引擎模块模型 - 策略定义、配置、运行日志
"""
from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text, Float, Enum as SAEnum, JSON, Index
from sqlalchemy.orm import relationship
import enum

from app.core.database import Base


class StrategyType(str, enum.Enum):
    """策略类型"""
    MA_CROSS = "ma_cross"            # 均线交叉
    MACD = "macd"                     # MACD
    KDJ = "kdj"                       # KDJ
    BOLLINGER = "bollinger"           # 布林带
    GRID = "grid"                     # 网格交易
    MARTINGALE = "martingale"         # 马丁格尔
    TREND_BREAK = "trend_break"       # 趋势突破
    CUSTOM = "custom"                 # 自定义策略


class StrategyStatus(str, enum.Enum):
    """策略状态"""
    DRAFT = "draft"                   # 草稿
    ACTIVE = "active"                 # 运行中
    PAUSED = "paused"                 # 已暂停
    STOPPED = "stopped"               # 已停止
    ERROR = "error"                   # 异常


class Strategy(Base):
    """策略表"""
    __tablename__ = "strategies"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, comment="用户ID")
    name = Column(String(100), nullable=False, comment="策略名称")
    type = Column(SAEnum(StrategyType), nullable=False, comment="策略类型")
    description = Column(Text, nullable=True, comment="策略描述")
    status = Column(SAEnum(StrategyStatus), default=StrategyStatus.DRAFT, comment="策略状态")

    # 策略参数 (JSON)
    # 包含: 标的、时间周期、各项指标参数、风控参数等
    params = Column(JSON, default=dict, comment="策略参数")

    # 是否为代码自定义策略
    is_custom_code = Column(Boolean, default=False, comment="是否自定义代码策略")
    custom_code = Column(Text, nullable=True, comment="自定义策略代码")

    # 资金配置
    initial_capital = Column(Float, default=10000.0, comment="初始资金")
    max_position_ratio = Column(Integer, default=30, comment="最大仓位比例(%)")

    # 运行配置
    symbols = Column(JSON, default=list, comment="交易标的列表")
    intervals = Column(JSON, default=list, comment="时间周期列表")
    schedule_config = Column(JSON, default=dict, comment="调度配置: {enabled, type, interval_minutes, cron_expression, active_hours, active_days}")

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), comment="创建时间")
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), comment="更新时间")

    # 关联
    user = relationship("User", back_populates="strategies")
    run_logs = relationship("StrategyRunLog", back_populates="strategy", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Strategy(id={self.id}, name={self.name}, type={self.type}, status={self.status})>"


class StrategyRunLog(Base):
    """策略运行日志"""
    __tablename__ = "strategy_run_logs"
    __table_args__ = (
        Index("idx_strategy_run_logs_sid_time", "strategy_id", "run_time"),
    )

    id = Column(Integer, primary_key=True, index=True)
    strategy_id = Column(Integer, ForeignKey("strategies.id", ondelete="CASCADE"), nullable=False, comment="策略ID")
    run_time = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), comment="运行时间")
    status = Column(String(20), default="success", comment="状态: success/error")
    message = Column(Text, nullable=True, comment="运行消息")
    signals = Column(JSON, nullable=True, comment="产生的交易信号")
    duration_ms = Column(Integer, nullable=True, comment="运行耗时(毫秒)")

    strategy = relationship("Strategy", back_populates="run_logs")
