"""
价格提醒接口 - CRUD"""
from __future__ import annotations
from typing import List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user
from app.models.user import User
from app.schemas.common import Response
from app.schemas.price_alert import PriceAlertCreate, PriceAlertUpdate, PriceAlertResponse
from app.services.alert import (
    create_alert,
    get_alerts,
    get_alert,
    update_alert,
    delete_alert,
    reset_alert,
)

router = APIRouter(prefix="/alerts", tags=["价格提醒"])


@router.post("", response_model=Response[PriceAlertResponse])
async def create_price_alert(
    req: PriceAlertCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        alert = await create_alert(
            db, current_user.id, req.symbol, req.condition, req.target_price, req.message
        )
        return Response(data=PriceAlertResponse.model_validate(alert), message="提醒创建成功")
    except Exception:
        raise HTTPException(status_code=409, detail="提醒已存在或创建失败")


@router.get("", response_model=Response[List[PriceAlertResponse]])
async def list_alerts(
    skip: int = 0,
    limit: int = 50,
    status: str = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    alerts = await get_alerts(db, current_user.id, skip, limit, status)
    return Response(data=[PriceAlertResponse.model_validate(a) for a in alerts])


@router.get("/{alert_id}", response_model=Response[PriceAlertResponse])
async def get_alert_detail(
    alert_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    alert = await get_alert(db, alert_id, current_user.id)
    if not alert:
        raise HTTPException(status_code=404, detail="提醒不存在")
    return Response(data=PriceAlertResponse.model_validate(alert))


@router.put("/{alert_id}", response_model=Response[PriceAlertResponse])
async def update_price_alert(
    alert_id: int,
    req: PriceAlertUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    alert = await update_alert(
        db, alert_id, current_user.id, **req.model_dump(exclude_unset=True)
    )
    if not alert:
        raise HTTPException(status_code=404, detail="提醒不存在")
    return Response(data=PriceAlertResponse.model_validate(alert), message="更新成功")


@router.delete("/{alert_id}", response_model=Response[dict])
async def delete_price_alert(
    alert_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ok = await delete_alert(db, alert_id, current_user.id)
    if not ok:
        raise HTTPException(status_code=404, detail="提醒不存在")
    return Response(data={}, message="提醒已删除")


@router.put("/{alert_id}/reset", response_model=Response[PriceAlertResponse])
async def reset_price_alert(
    alert_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    alert = await reset_alert(db, alert_id, current_user.id)
    if not alert:
        raise HTTPException(status_code=404, detail="提醒不存在")
    return Response(data=PriceAlertResponse.model_validate(alert), message="提醒已重置")
