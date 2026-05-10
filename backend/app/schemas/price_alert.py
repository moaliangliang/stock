"""
价格提醒 Pydantic 校验模型
"""
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class PriceAlertCreate(BaseModel):
    """创建价格提醒"""
    symbol: str = Field(..., min_length=1, max_length=50, description="标的代码")
    condition: str = Field(..., pattern="^(above|below)$", description="触发条件: above/below")
    target_price: float = Field(..., gt=0, description="目标价格")
    message: Optional[str] = Field(None, max_length=200, description="自定义提醒内容")


class PriceAlertUpdate(BaseModel):
    """更新价格提醒"""
    condition: Optional[str] = Field(None, pattern="^(above|below)$")
    target_price: Optional[float] = Field(None, gt=0)
    status: Optional[str] = Field(None, pattern="^(active|disabled)$")
    message: Optional[str] = Field(None, max_length=200)


class PriceAlertResponse(BaseModel):
    """价格提醒响应"""
    id: int
    user_id: int
    symbol: str
    condition: str
    target_price: float
    status: str
    triggered_at: Optional[datetime] = None
    triggered_price: Optional[float] = None
    message: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
