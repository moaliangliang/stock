"""
行情定时任务 - 更新行情数据、数据清理
"""
from datetime import datetime, timezone, timedelta
from loguru import logger

from app.core.celery_app import celery_app
from app.core.database import SyncSessionLocal
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
