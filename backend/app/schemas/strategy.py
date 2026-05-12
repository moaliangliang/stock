"""
策略模块 Schema
"""
from typing import Any, Dict, List, Optional
from datetime import datetime
from pydantic import BaseModel, Field

from app.models.strategy import StrategyType, StrategyStatus


class StrategyCreate(BaseModel):
    """创建策略"""
    name: str = Field(..., min_length=1, max_length=100)
    type: StrategyType
    description: Optional[str] = None
    params: Dict[str, Any] = {}
    symbols: List[str] = []
    intervals: List[str] = ["1d"]
    initial_capital: float = 10000.0
    max_position_ratio: int = 30
    is_custom_code: bool = False
    custom_code: Optional[str] = Field(None, max_length=65536)
    schedule_config: Dict[str, Any] = {}


class StrategyUpdate(BaseModel):
    """更新策略"""
    name: Optional[str] = None
    description: Optional[str] = None
    params: Optional[Dict[str, Any]] = None
    symbols: Optional[List[str]] = None
    intervals: Optional[List[str]] = None
    initial_capital: Optional[float] = None
    max_position_ratio: Optional[int] = None
    status: Optional[StrategyStatus] = None
    is_custom_code: Optional[bool] = None
    custom_code: Optional[str] = Field(None, max_length=65536)
    schedule_config: Optional[Dict[str, Any]] = None


class StrategyResponse(BaseModel):
    """策略响应"""
    id: int
    user_id: int
    name: str
    type: StrategyType
    description: Optional[str] = None
    status: StrategyStatus
    params: Dict[str, Any]
    symbols: List[str]
    intervals: List[str]
    initial_capital: float
    max_position_ratio: int
    is_custom_code: bool
    custom_code: Optional[str] = None
    schedule_config: Dict[str, Any] = {}
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class StrategyRunLogResponse(BaseModel):
    """策略运行日志响应"""
    id: int
    strategy_id: int
    run_time: datetime
    status: str
    message: Optional[str] = None
    signals: Optional[Any] = None
    duration_ms: Optional[int] = None

    class Config:
        from_attributes = True


class BacktestRequest(BaseModel):
    """回测请求"""
    strategy_id: int
    symbol: str
    interval: str = "1d"
    start_date: str = Field(..., description="开始日期 YYYY-MM-DD")
    end_date: str = Field(..., description="结束日期 YYYY-MM-DD")
    initial_capital: float = 10000.0
    commission: float = 0.001
    slippage: float = 0.001


class BacktestResult(BaseModel):
    """回测结果"""
    total_return: float
    annual_return: float
    max_drawdown: float
    sharpe_ratio: float
    win_rate: float
    total_trades: int
    profit_trades: int
    loss_trades: int
    profit_factor: float
    equity_curve: List[List[float]]  # [[timestamp, equity], ...]
    trades: List[Dict[str, Any]]
