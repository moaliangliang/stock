"""
行情模块 Schema
"""
from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel, Field


class KLineResponse(BaseModel):
    """K线数据响应"""
    symbol: str
    interval: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float
    data_source: str = "unknown"

    class Config:
        from_attributes = True


class KLineQuery(BaseModel):
    """K线查询参数"""
    symbol: str = Field(..., description="标的代码")
    interval: str = Field("1d", description="时间周期")
    start_time: Optional[str] = Field(None, description="开始时间")
    end_time: Optional[str] = Field(None, description="结束时间")
    limit: int = Field(100, ge=1, le=1000, description="返回数量")


class TickerResponse(BaseModel):
    """实时行情响应"""
    symbol: str
    name: Optional[str] = None
    last_price: float
    bid: Optional[float] = None
    ask: Optional[float] = None
    bid_volume: Optional[float] = None
    ask_volume: Optional[float] = None
    high_24h: Optional[float] = None
    low_24h: Optional[float] = None
    volume_24h: Optional[float] = None
    change_24h: Optional[float] = None
    data_source: str = "unknown"
    updated_at: datetime

    class Config:
        from_attributes = True


class SymbolInfoResponse(BaseModel):
    """标的信息响应"""
    id: int
    symbol: str
    name: str
    exchange: str
    asset_type: str
    price_precision: int = 2
    qty_precision: int = 0
    min_qty: float = 0.0
    tick_size: float = 0.01
    status: str
    is_watched: bool = False

    class Config:
        from_attributes = True
