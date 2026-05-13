"""
实盘交易模型 - 订单、成交记录
"""
from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Enum as SAEnum, Text, Index
from sqlalchemy.orm import relationship
import enum

from app.core.database import Base


class OrderSide(str, enum.Enum):
    """订单方向"""
    BUY = "buy"
    SELL = "sell"


class OrderType(str, enum.Enum):
    """订单类型"""
    MARKET = "market"          # 市价单
    LIMIT = "limit"            # 限价单
    STOP = "stop"              # 止损单
    STOP_LIMIT = "stop_limit"  # 止损限价单


class OrderStatus(str, enum.Enum):
    """订单状态"""
    PENDING = "pending"           # 待成交
    PARTIAL = "partial"           # 部分成交
    FILLED = "filled"             # 全部成交
    CANCELED = "canceled"         # 已撤销
    REJECTED = "rejected"         # 已拒绝
    EXPIRED = "expired"           # 已过期


class Order(Base):
    """订单表"""
    __tablename__ = "orders"
    __table_args__ = (
        Index("idx_orders_user_status", "user_id", "status"),
        Index("idx_orders_symbol_time", "symbol", "created_at"),
        Index("idx_orders_updated_at", "updated_at"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, comment="用户ID")
    strategy_id = Column(Integer, ForeignKey("strategies.id", ondelete="SET NULL"), nullable=True, comment="关联策略ID")
    order_id_exchange = Column(String(100), nullable=True, comment="交易所订单ID")
    symbol = Column(String(20), nullable=False, comment="标的代码")
    side = Column(SAEnum(OrderSide), nullable=False, comment="买卖方向")
    type = Column(SAEnum(OrderType), default=OrderType.LIMIT, comment="订单类型")
    status = Column(SAEnum(OrderStatus), default=OrderStatus.PENDING, comment="订单状态")

    price = Column(Float, nullable=True, comment="下单价格(限价单)")
    stop_price = Column(Float, nullable=True, comment="触发价格(止损单)")
    quantity = Column(Float, nullable=False, comment="下单数量")
    filled_quantity = Column(Float, default=0, comment="已成交数量")
    avg_price = Column(Float, nullable=True, comment="成交均价")

    fee = Column(Float, default=0, comment="手续费")
    fee_asset = Column(String(10), nullable=True, comment="手续费币种")

    source = Column(String(20), default="manual", comment="订单来源: manual/strategy/api")
    remark = Column(Text, nullable=True, comment="备注")

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), comment="创建时间")
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), comment="更新时间")

    user = relationship("User", back_populates="orders")
    trades = relationship("Trade", back_populates="order", cascade="all, delete-orphan")


class Trade(Base):
    """成交记录表"""
    __tablename__ = "trades"
    __table_args__ = (
        Index("idx_trades_order_id", "order_id"),
        Index("idx_trades_trade_time", "trade_time"),
    )

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, comment="订单ID")
    trade_id_exchange = Column(String(100), nullable=True, comment="交易所成交ID")
    symbol = Column(String(20), nullable=False, comment="标的代码")
    side = Column(SAEnum(OrderSide), nullable=False, comment="买卖方向")
    price = Column(Float, nullable=False, comment="成交价格")
    quantity = Column(Float, nullable=False, comment="成交数量")
    fee = Column(Float, default=0, comment="手续费")
    fee_asset = Column(String(10), nullable=True, comment="手续费币种")
    trade_time = Column(DateTime(timezone=True), nullable=False, comment="成交时间")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    order = relationship("Order", back_populates="trades")
