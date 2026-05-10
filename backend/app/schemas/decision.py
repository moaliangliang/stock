"""
投资决策建议 Pydantic Schema
"""
from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class DecisionGenerateRequest(BaseModel):
    """生成决策请求"""
    symbols: List[str] = Field(..., min_length=1, description="股票代码列表")


class DecisionFactorDetail(BaseModel):
    """因子详情"""
    score: float = Field(description="因子得分 0-100")
    weight: float = Field(description="因子权重")
    label: str = Field(description="因子名称")
    details: Dict[str, Any] = Field(default_factory=dict, description="因子详细数据")


class DecisionFactors(BaseModel):
    """多因子分析结果"""
    technical_score: float
    sentiment_score: float
    risk_score: float
    momentum_score: float
    fundamental_score: float = 0.0
    composite_score: float
    technical: DecisionFactorDetail
    sentiment: DecisionFactorDetail
    risk: DecisionFactorDetail
    momentum: DecisionFactorDetail
    fundamental: Optional[DecisionFactorDetail] = None
    regime: Optional[str] = None
    weights: Optional[Dict[str, float]] = None
    weekly_technical: Optional[DecisionFactorDetail] = None


class DecisionResponse(BaseModel):
    """决策响应"""
    id: int
    user_id: int
    symbol: str
    recommendation: str
    confidence: int
    target_price: Optional[float] = None
    stop_loss: Optional[float] = None
    factors: Optional[Dict[str, Any]] = None
    reasoning: Optional[str] = None
    status: str
    valid_until: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class DecisionSummaryResponse(BaseModel):
    """决策汇总统计"""
    total_active: int = 0
    strong_buy_count: int = 0
    buy_count: int = 0
    hold_count: int = 0
    sell_count: int = 0
    strong_sell_count: int = 0
    avg_confidence: float = 0.0
    top_picks: List[DecisionResponse] = Field(default_factory=list)
    recent_decisions: List[DecisionResponse] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Outcome tracking schemas (P3)
# ---------------------------------------------------------------------------


class DecisionOutcomeResponse(BaseModel):
    """决策结果响应"""
    id: int
    decision_id: int
    symbol: str
    recommendation: str
    confidence: int
    entry_price: Optional[float] = None
    actual_high_24h: Optional[float] = None
    actual_low_24h: Optional[float] = None
    actual_close_24h: Optional[float] = None
    hit_target: bool = False
    hit_stop: bool = False
    pnl_pct: Optional[float] = None
    outcome: Optional[str] = None
    checked_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class OutcomeSummaryResponse(BaseModel):
    """准确率汇总"""
    total: int = 0
    wins: int = 0
    losses: int = 0
    breakeven_count: int = 0
    win_rate: float = 0.0
    avg_pnl_pct: float = 0.0
    strong_buy_accuracy: float = 0.0
    buy_accuracy: float = 0.0
    hold_accuracy: float = 0.0
    sell_accuracy: float = 0.0
    strong_sell_accuracy: float = 0.0
    recent_outcomes: List[DecisionOutcomeResponse] = Field(default_factory=list)
