"""
行情定时任务 - 更新行情数据、数据清理
"""
from datetime import datetime, timezone, timedelta
from loguru import logger

from app.core.celery_app import celery_app
from app.core.database import SyncSessionLocal
from app.core.redis import TaskLock
from app.services.data_provider import refresh_all_tickers
from app.models.market_data import KLine


@celery_app.task(queue="market")
def update_market_data():
    """定时更新行情数据 - 刷新所有标的最新行情"""
    logger.info(f"开始更新行情数据: {datetime.now(timezone.utc)}")
    db = SyncSessionLocal()
    try:
        refresh_all_tickers(db)
        db.commit()
        logger.info("行情数据更新完成")
    except Exception as e:
        db.rollback()
        logger.error(f"行情更新失败: {e}")
    finally:
        db.close()


@celery_app.task(queue="market")
def sync_eastmoney_positions():
    """定时从东方财富账号同步持仓"""
    from app.core.config import settings
    if not settings.EM_ACCOUNT_SYNC_ENABLED:
        return

    with TaskLock("sync_eastmoney_positions", timeout=300) as acquired:
        if not acquired:
            return "Skipped: another instance is running"

        logger.info("开始同步东方财富持仓")
        db = SyncSessionLocal()
        try:
            # 使用同步方式调用（Celery 任务运行在同步上下文）
            import asyncio
            from app.services.trade import sync_positions_from_eastmoney
            from app.core.database import AsyncSessionLocal

            async def _run():
                async with AsyncSessionLocal() as async_db:
                    return await sync_positions_from_eastmoney(async_db, user_id=1)

            result = asyncio.get_event_loop().run_until_complete(_run())
            logger.info(
                "东方财富持仓同步完成: 新建 %s, 更新 %s, 共 %s 条",
                result.get("created", 0),
                result.get("updated", 0),
                result.get("total", 0),
            )
        except Exception as e:
            logger.error(f"东方财富持仓同步失败: {e}")
        finally:
            db.close()


@celery_app.task(queue="maintenance")
def cleanup_old_data(days: int = 90):
    """清理过期 K 线数据"""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    logger.info(f"开始清理 {days} 天前的数据 (截止: {cutoff})")
    db = SyncSessionLocal()
    try:
        from sqlalchemy import delete
        stmt = delete(KLine).where(KLine.timestamp < cutoff)
        result = db.execute(stmt)
        db.commit()
        logger.info(f"数据清理完成，删除了 {result.rowcount} 条记录")
    except Exception as e:
        db.rollback()
        logger.error(f"数据清理失败: {e}")
    finally:
        db.close()
