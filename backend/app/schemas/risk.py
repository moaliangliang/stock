"""
风控模块 Schema
"""
from typing import Optional

from pydantic import BaseModel, Field

from app.models.risk import RiskAction, RiskRuleType


class RiskRuleCreate(BaseModel):
    """创建风控规则"""
    name: str = Field(..., min_length=1, max_length=100)
    rule_type: RiskRuleType
    action: RiskAction = RiskAction.WARN
    is_active: bool = True
    params: Optional[dict] = None
    symbols: Optional[str] = None  # comma-separated, None = all
    description: Optional[str] = None


class RiskRuleUpdate(BaseModel):
    """更新风控规则"""
    name: Optional[str] = None
    rule_type: Optional[RiskRuleType] = None
    action: Optional[RiskAction] = None
    is_active: Optional[bool] = None
    params: Optional[dict] = None
    symbols: Optional[str] = None
    description: Optional[str] = None
