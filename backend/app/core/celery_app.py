"""
Celery 任务队列配置 - 定时策略、定时回测、行情订阅、持仓推送
"""
from celery import Celery
from celery.schedules import crontab

from app.core.config import settings

celery_app = Celery(
    "quant_tasks",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=["app.tasks"],
)

# Celery 配置
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Shanghai",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=30 * 60,  # 30分钟
    task_soft_time_limit=20 * 60,  # 20分钟软限制
    worker_max_tasks_per_child=100,
    beat_schedule={
        # 行情数据定时更新 — 由 WebSocket broadcast 负责，Celery 侧暂禁用以避免 SQLite 锁竞争
        # "market-data-update": {
        #     "task": "app.tasks.market.update_market_data",
        #     "schedule": 5.0,
        #     "options": {"queue": "market"},
        # },
        # 策略定时执行（每分钟）
        "strategy-scheduled-run": {
            "task": "app.tasks.strategy.run_scheduled_strategies",
            "schedule": 60.0,
            "options": {"queue": "strategy"},
        },
        # 价格提醒检测（每10秒）
        "price-alert-check": {
            "task": "app.tasks.alert.check_price_alerts",
            "schedule": 10.0,
            "options": {"queue": "market"},
        },
        # 持仓监控推送（工作日 9:00-15:55 每5分钟，脚本内部判断半小时节点推送）
        "stock-push": {
            "task": "app.tasks.stock_push.run_stock_push",
            "schedule": crontab(minute="*/5", hour="9-15", day_of_week="1-5"),
            "options": {"queue": "market"},
        },
        # 买入信号扫描（每3分钟，仅工作时间执行；任务内部判断市场时段）
        "signal-scanner": {
            "task": "app.tasks.signal_scanner.run_signal_scanner",
            "schedule": crontab(minute="*/3", hour="9-15", day_of_week="1-5"),
            "options": {"queue": "market"},
        },
        # 投资决策生成（每5分钟，仅市场时段内执行）
        "investment-decision-generate": {
            "task": "app.tasks.decision.generate_investment_decisions",
            "schedule": 300.0,  # 5分钟
            "options": {"queue": "strategy"},
        },
        # 决策结果追踪（每30分钟）
        "check-decision-outcomes": {
            "task": "app.tasks.decision.check_decision_outcomes",
            "schedule": crontab(minute="*/30"),
            "options": {"queue": "strategy"},
        },
        # 每日数据清理
        "daily-data-cleanup": {
            "task": "app.tasks.market.cleanup_old_data",
            "schedule": 86400.0,  # 24小时
            "options": {"queue": "maintenance"},
        },
    },
)
