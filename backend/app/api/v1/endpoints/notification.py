"""
通知接口 - 列表、未读计数、标记已读"""
from __future__ import annotations
from typing import List
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user
from app.models.user import User
from app.schemas.common import Response
from app.schemas.notification import NotificationResponse
from app.services.notification import (
    get_notifications,
    get_unread_count,
    mark_notification_read,
    mark_all_read,
)

router = APIRouter(prefix="/notifications", tags=["通知管理"])


@router.get("", response_model=Response[List[NotificationResponse]])
async def list_notifications(
    skip: int = 0,
    limit: int = 50,
    unread_only: bool = False,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取通知列表"""
    notifications = await get_notifications(db, current_user.id, skip, limit, unread_only)
    return Response(data=[NotificationResponse.model_validate(n) for n in notifications])


@router.get("/unread-count", response_model=Response[dict])
async def unread_count(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取未读通知数量"""
    count = await get_unread_count(db, current_user.id)
    return Response(data={"count": count})


@router.put("/{notification_id}/read", response_model=Response[dict])
async def mark_read(
    notification_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """标记通知为已读"""
    ok = await mark_notification_read(db, notification_id, current_user.id)
    return Response(data={}, message="已标记为已读" if ok else "通知不存在")


@router.put("/read-all", response_model=Response[dict])
async def mark_all_read_endpoint(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """标记所有通知为已读"""
    count = await mark_all_read(db, current_user.id)
    return Response(data={"marked": count}, message=f"已标记 {count} 条通知为已读")
