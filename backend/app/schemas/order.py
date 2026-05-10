"""
交易模块 Schema
"""
from typing import Optional
from datetime import datetime
from pydantic import BaseModel, Field

from app.models.order import OrderSide, OrderType, OrderStatus


class OrderCreate(BaseModel):
    """创建订单"""
    symbol: str = Field(..., description="标的代码")
    side: OrderSide
    type: OrderType = OrderType.LIMIT
    price: Optional[float] = Field(None, description="价格(限价单必填)")
    stop_price: Optional[float] = Field(None, description="触发价(止损单)")
    quantity: float = Field(..., gt=0, description="数量")
    strategy_id: Optional[int] = Field(None, description="关联策略ID")


class OrderResponse(BaseModel):
    """订单响应"""
    id: int
    user_id: int
    strategy_id: Optional[int] = None
    order_id_exchange: Optional[str] = None
    symbol: str
    side: OrderSide
    type: OrderType
    status: OrderStatus
    price: Optional[float] = None
    stop_price: Optional[float] = None
    quantity: float
    filled_quantity: float
    avg_price: Optional[float] = None
    fee: float
    source: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class TradeResponse(BaseModel):
    """成交记录响应"""
    id: int
    order_id: int
    symbol: str
    side: OrderSide
    price: float
    quantity: float
    fee: float
    fee_asset: Optional[str] = None
    trade_time: datetime

    class Config:
        from_attributes = True


class PositionResponse(BaseModel):
    """持仓响应"""
    id: int
    user_id: int
    symbol: str
    quantity: float
    available_quantity: float
    frozen_quantity: float
    cost_price: float
    current_price: float
    market_value: float
    pnl: float
    pnl_ratio: float
    day_pnl: float
    day_pnl_ratio: float
    updated_at: datetime

    class Config:
        from_attributes = True


class PositionCreate(BaseModel):
    """手动创建/更新持仓"""
    symbol: str = Field(..., description="标的代码")
    quantity: float = Field(..., gt=0, description="持仓数量")
    cost_price: float = Field(..., gt=0, description="成本价")
    leverage: float = Field(1.0, ge=1.0, description="杠杆倍数")


class PositionImportRow(BaseModel):
    """Excel 导入行结果"""
    row: int
    symbol: str
    status: str  # created / updated / error
    message: str = ""


class PositionImportResult(BaseModel):
    """Excel 导入结果"""
    total: int
    created: int
    updated: int
    errors: int
    details: list[PositionImportRow] = []


class CancelOrderRequest(BaseModel):
    """撤单请求"""
    order_id: int
