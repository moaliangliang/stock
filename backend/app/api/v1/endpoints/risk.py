"""
风控接口 - 风控规则管理、风控记录查询"""
from __future__ import annotations
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user
from app.schemas.common import Response
from app.models.user import User
from app.models.risk import RiskRuleType, RiskAction
from app.services.risk import (
    get_risk_rules,
    create_risk_rule,
    update_risk_rule,
    delete_risk_rule,
    get_risk_records,
)

router = APIRouter(prefix="/risk", tags=["风控管理"])


@router.get("/rules", response_model=Response[List[dict]])
async def list_risk_rules(
    rule_type: Optional[RiskRuleType] = Query(None, description="按规则类型过滤"),
    is_active: Optional[bool] = Query(None, description="按启用状态过滤"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取风控规则列表"""
    rules = await get_risk_rules(db, rule_type=rule_type, is_active=is_active)
    return Response(data=[_rule_to_dict(r) for r in rules])


@router.post("/rules", response_model=Response[dict])
async def create_risk_rule_endpoint(
    req: dict,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """创建风控规则"""
    rule = await create_risk_rule(db, user_id=current_user.id, rule_data=req)
    return Response(data=_rule_to_dict(rule), message="风控规则创建成功")


@router.put("/rules/{rule_id}", response_model=Response[dict])
async def update_risk_rule_endpoint(
    rule_id: int,
    req: dict,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """更新风控规则"""
    rule = await update_risk_rule(db, rule_id, req)
    if not rule:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="风控规则不存在")
    return Response(data=_rule_to_dict(rule), message="风控规则更新成功")


@router.delete("/rules/{rule_id}", response_model=Response)
async def delete_risk_rule_endpoint(
    rule_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """删除风控规则"""
    success = await delete_risk_rule(db, rule_id)
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="风控规则不存在")
    return Response(message="风控规则已删除")


@router.get("/records", response_model=Response[List[dict]])
async def list_risk_records(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取风控触发记录"""
    records = await get_risk_records(db, user_id=current_user.id, skip=skip, limit=limit)
    return Response(data=[_record_to_dict(r) for r in records])


def _rule_to_dict(rule) -> dict:
    return {
        "id": rule.id,
        "name": rule.name,
        "rule_type": rule.rule_type.value if hasattr(rule.rule_type, 'value') else rule.rule_type,
        "action": rule.action.value if hasattr(rule.action, 'value') else rule.action,
        "is_active": rule.is_active,
        "params": rule.params,
        "symbols": rule.symbols,
        "description": rule.description,
        "created_at": str(rule.created_at) if rule.created_at else None,
    }


def _record_to_dict(record) -> dict:
    return {
        "id": record.id,
        "rule_id": record.rule_id,
        "user_id": record.user_id,
        "symbol": record.symbol,
        "action": record.action.value if hasattr(record.action, 'value') else record.action,
        "trigger_value": record.trigger_value,
        "limit_value": record.limit_value,
        "message": record.message,
        "created_at": str(record.created_at) if record.created_at else None,
    }
