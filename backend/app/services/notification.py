"""
通知服务 - 创建、查询、标记已读"""
from __future__ import annotations
import logging
from typing import Optional
from datetime import datetime, timezone

from sqlalchemy import select, desc, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import Notification

logger = logging.getLogger(__name__)


async def create_notification(
    db: AsyncSession,
    user_id: int,
    type: str,
    title: str,
    content: Optional[str] = None,
    metadata_json: Optional[dict] = None,
) -> Notification:
    """创建通知"""
    notification = Notification(
        user_id=user_id,
        type=type,
        title=title,
        content=content,
        metadata_json=metadata_json or {},
        is_read=False,
    )
    db.add(notification)
    await db.flush()
    await db.refresh(notification)
    return notification


async def get_notifications(
    db: AsyncSession,
    user_id: int,
    skip: int = 0,
    limit: int = 50,
    unread_only: bool = False,
) -> list[Notification]:
    """获取用户通知列表"""
    query = select(Notification).where(Notification.user_id == user_id)
    if unread_only:
        query = query.where(Notification.is_read == False)
    query = query.order_by(desc(Notification.created_at)).offset(skip).limit(limit)
    result = await db.execute(query)
    return list(result.scalars().all())


async def get_unread_count(db: AsyncSession, user_id: int) -> int:
    """获取未读通知数量"""
    query = select(func.count(Notification.id)).where(
        Notification.user_id == user_id,
        Notification.is_read == False,
    )
    result = await db.execute(query)
    return result.scalar() or 0


async def mark_notification_read(db: AsyncSession, notification_id: int, user_id: int) -> bool:
    """标记单条通知为已读"""
    result = await db.execute(
        select(Notification).where(
            Notification.id == notification_id,
            Notification.user_id == user_id,
        )
    )
    notification = result.scalar_one_or_none()
    if not notification:
        return False
    notification.is_read = True
    await db.flush()
    return True


async def mark_all_read(db: AsyncSession, user_id: int) -> int:
    """标记所有通知为已读，返回更新的数量"""
    from sqlalchemy import update
    result = await db.execute(
        update(Notification)
        .where(Notification.user_id == user_id, Notification.is_read == False)
        .values(is_read=True)
    )
    await db.flush()
    return result.rowcount
