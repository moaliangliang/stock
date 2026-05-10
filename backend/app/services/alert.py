"""
价格提醒服务 - 创建、查询、更新、删除、定时检测"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.models.price_alert import PriceAlert
from app.models.market_data import Ticker
from app.models.notification import Notification


async def create_alert(
    db: AsyncSession,
    user_id: int,
    symbol: str,
    condition: str,
    target_price: float,
    message: Optional[str] = None,
) -> PriceAlert:
    alert = PriceAlert(
        user_id=user_id,
        symbol=symbol,
        condition=condition,
        target_price=target_price,
        message=message,
    )
    db.add(alert)
    await db.flush()
    await db.refresh(alert)
    return alert


async def get_alerts(
    db: AsyncSession,
    user_id: int,
    skip: int = 0,
    limit: int = 50,
    status: Optional[str] = None,
) -> list[PriceAlert]:
    query = select(PriceAlert).where(PriceAlert.user_id == user_id)
    if status:
        query = query.where(PriceAlert.status == status)
    query = query.order_by(PriceAlert.created_at.desc()).offset(skip).limit(limit)
    result = await db.execute(query)
    return list(result.scalars().all())


async def get_alert(db: AsyncSession, alert_id: int, user_id: int) -> Optional[PriceAlert]:
    result = await db.execute(
        select(PriceAlert).where(
            PriceAlert.id == alert_id,
            PriceAlert.user_id == user_id,
        )
    )
    return result.scalar_one_or_none()


async def update_alert(
    db: AsyncSession,
    alert_id: int,
    user_id: int,
    **kwargs,
) -> Optional[PriceAlert]:
    alert = await get_alert(db, alert_id, user_id)
    if not alert:
        return None
    for key, value in kwargs.items():
        if value is not None:
            setattr(alert, key, value)
    alert.updated_at = datetime.now(timezone.utc)
    await db.flush()
    await db.refresh(alert)
    return alert


async def delete_alert(db: AsyncSession, alert_id: int, user_id: int) -> bool:
    alert = await get_alert(db, alert_id, user_id)
    if not alert:
        return False
    await db.delete(alert)
    await db.flush()
    return True


async def reset_alert(db: AsyncSession, alert_id: int, user_id: int) -> Optional[PriceAlert]:
    alert = await get_alert(db, alert_id, user_id)
    if not alert:
        return None
    alert.status = "active"
    alert.triggered_at = None
    alert.triggered_price = None
    alert.updated_at = datetime.now(timezone.utc)
    await db.flush()
    await db.refresh(alert)
    return alert


def check_price_alerts(db: Session) -> int:
    """检测所有活跃提醒，触发时创建通知。同步函数，供 Celery 任务调用。"""
    alerts = db.execute(
        select(PriceAlert).where(PriceAlert.status == "active")
    ).scalars().all()

    if not alerts:
        return 0

    symbols = list({a.symbol for a in alerts})
    ticker_rows = db.execute(
        select(Ticker).where(Ticker.symbol.in_(symbols))
    ).scalars().all()
    tickers = {t.symbol: t for t in ticker_rows}

    triggered_count = 0
    now = datetime.now(timezone.utc)

    for alert in alerts:
        ticker = tickers.get(alert.symbol)
        if not ticker:
            continue

        price = ticker.last_price
        triggered = False
        if alert.condition == "above" and price >= alert.target_price:
            triggered = True
        elif alert.condition == "below" and price <= alert.target_price:
            triggered = True

        if not triggered:
            continue

        alert.status = "triggered"
        alert.triggered_at = now
        alert.triggered_price = price
        alert.updated_at = now

        condition_text = "上穿" if alert.condition == "above" else "下穿"
        title = f"价格提醒: {alert.symbol} {condition_text} {alert.target_price}"
        content = alert.message or f"{alert.symbol} 当前价格 {price} 已{condition_text}目标价 {alert.target_price}"

        notification = Notification(
            user_id=alert.user_id,
            type="system",
            title=title,
            content=content,
            metadata_json={
                "alert_id": alert.id,
                "symbol": alert.symbol,
                "condition": alert.condition,
                "target_price": alert.target_price,
                "triggered_price": price,
            },
        )
        db.add(notification)
        triggered_count += 1

    if triggered_count:
        db.flush()
    return triggered_count
