"""
投资决策建议模型
"""
from datetime import datetime, timezone
from sqlalchemy import Boolean, Column, Integer, String, Float, DateTime, ForeignKey, Text, Enum as SAEnum, JSON
from sqlalchemy.orm import relationship
import enum

from app.core.database import Base


class DecisionRecommendation(str, enum.Enum):
    STRONG_BUY = "strong_buy"
    BUY = "buy"
    HOLD = "hold"
    SELL = "sell"
    STRONG_SELL = "strong_sell"


class DecisionStatus(str, enum.Enum):
    ACTIVE = "active"
    EXECUTED = "executed"
    DISMISSED = "dismissed"
    EXPIRED = "expired"


class OutcomeType(str, enum.Enum):
    WIN = "win"
    LOSS = "loss"
    BREAKEVEN = "breakeven"


class InvestmentDecision(Base):
    """投资决策建议表"""
    __tablename__ = "investment_decisions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, comment="用户ID")
    symbol = Column(String(20), nullable=False, comment="股票代码")
    recommendation = Column(SAEnum(DecisionRecommendation), nullable=False, comment="建议类型")
    confidence = Column(Integer, default=50, comment="置信度 0-100")
    target_price = Column(Float, nullable=True, comment="目标价格")
    stop_loss = Column(Float, nullable=True, comment="止损价格")
    factors = Column(JSON, default=dict, comment="因子分析详情")
    reasoning = Column(Text, nullable=True, comment="分析推理过程")
    status = Column(SAEnum(DecisionStatus), default=DecisionStatus.ACTIVE, comment="状态")
    valid_until = Column(DateTime(timezone=True), nullable=True, comment="有效期至")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class DecisionOutcome(Base):
    """决策结果追踪表 — 追踪每条投资决策的准确性"""
    __tablename__ = "decision_outcomes"

    id = Column(Integer, primary_key=True, index=True)
    decision_id = Column(Integer, ForeignKey("investment_decisions.id"), unique=True, nullable=False, comment="关联决策ID")
    symbol = Column(String(20), nullable=False, comment="股票代码")
    recommendation = Column(String(20), nullable=False, comment="建议类型")
    confidence = Column(Integer, default=50, comment="置信度")
    entry_price = Column(Float, nullable=True, comment="决策时的参考价格")
    actual_high_24h = Column(Float, nullable=True, comment="24h最高价")
    actual_low_24h = Column(Float, nullable=True, comment="24h最低价")
    actual_close_24h = Column(Float, nullable=True, comment="24h收盘价")
    hit_target = Column(Boolean, default=False, comment="是否触及目标价")
    hit_stop = Column(Boolean, default=False, comment="是否触及止损价")
    pnl_pct = Column(Float, nullable=True, comment="实际盈亏百分比")
    outcome = Column(SAEnum(OutcomeType), nullable=True, comment="结果: win/loss/breakeven")
    checked_at = Column(DateTime(timezone=True), nullable=True, comment="检查时间")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
