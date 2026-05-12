"""DCA 定投回测请求/响应模型"""
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class DcaBacktestRequest(BaseModel):
    """DCA 回测请求"""

    symbols: List[str] = Field(
        default=["513100.SH", "513500.SH"],
        description="ETF代码列表，默认纳斯达克+标普500",
    )
    start_date: str = Field(
        default="2021-01-01",
        description="起始日期 YYYY-MM-DD",
    )
    end_date: str = Field(
        default="2026-05-11",
        description="结束日期 YYYY-MM-DD",
    )
    amount: float = Field(
        default=1000.0,
        ge=100,
        description="每期基准定投金额(CNY)",
    )
    mode: str = Field(
        default="fixed",
        description='定投模式: "fixed" 定额 | "smart" 智能 | "wait" 等待250MA',
    )
    smart_aggressiveness: float = Field(
        default=2.0,
        ge=0.5,
        le=5.0,
        description="智能模式乘数敏感度",
    )
    smart_min_multiplier: float = Field(
        default=0.5,
        ge=0.1,
        le=1.0,
        description="智能模式最低投入倍数",
    )
    smart_max_multiplier: float = Field(
        default=2.0,
        ge=1.0,
        le=5.0,
        description="智能模式最高投入倍数",
    )


class DcaMetrics(BaseModel):
    """单个标的 DCA 指标"""

    name: str
    symbol: str
    total_invested: float
    final_value: float
    total_return_pct: float
    annualized_xirr_pct: Optional[float]
    max_drawdown_pct: float
    investment_count: int
    avg_invest_amount: float
    last_price: float
    cost_basis: float


class LumpsumResult(BaseModel):
    """一次性投入对比结果"""

    name: str
    total_invested: float
    final_value: float
    total_return_pct: float
    annual_return_pct: float
    buy_price: float
    current_price: float
    buy_date: str
    end_date: str
    days_held: int


class MonthlyEntry(BaseModel):
    """月度市值条目"""

    year: int
    month: int
    label: str
    portfolio: float


class DcaBacktestResponse(BaseModel):
    """DCA 回测响应"""

    results: Dict[str, Dict[str, Any]]
    lumpsum: Dict[str, Dict[str, Any]]
    comparison: Dict[str, Any]
    monthly_series: List[Dict[str, Any]]
    params: Dict[str, Any]
    errors: Optional[List[str]]
