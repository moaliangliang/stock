"""
价格提醒定时任务 - 检测活跃提醒并触发通知
"""
from datetime import datetime, timezone

from loguru import logger

from app.core.celery_app import celery_app
from app.core.database import SyncSessionLocal


@celery_app.task(queue="market")
def check_price_alerts():
    """定时检测价格提醒"""
    logger.info(f"开始检测价格提醒: {datetime.now(timezone.utc)}")
    db = SyncSessionLocal()
    try:
        from app.services.alert import check_price_alerts as _check
        count = _check(db)
        db.commit()
        if count:
            logger.info(f"价格提醒检测完成，触发 {count} 条")
    except Exception as e:
        db.rollback()
        logger.error(f"价格提醒检测失败: {e}")
    finally:
        db.close()
