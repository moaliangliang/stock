"""
Celery 任务队列配置 - 定时策略、定时回测、行情订阅、持仓推送
"""
from celery import Celery
from celery.schedules import crontab

from app.core.config import settings

celery_app = Celery(
    "quant_tasks",
    broker=settings._celery_broker_url,
    backend=settings._celery_result_backend,
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
        # 东方财富持仓同步（工作日 9:00-15:55 每5分钟）
        "eastmoney-position-sync": {
            "task": "app.tasks.market.sync_eastmoney_positions",
            "schedule": crontab(minute="*/5", hour="9-15", day_of_week="1-5"),
            "options": {"queue": "market"},
        },
        # 持仓监控推送（工作日 9:00-15:55 每5分钟，脚本内部判断半小时节点推送）
        "stock-push": {
            "task": "app.tasks.stock_push.run_stock_push",
            "schedule": crontab(minute="*/5", hour="9-15", day_of_week="1-5"),
            "options": {"queue": "market"},
        },
        # 买入信号扫描（每5分钟兜底扫描；主力由 WebSocket broadcast 价格驱动实时触发）
        "signal-scanner": {
            "task": "app.tasks.signal_scanner.run_signal_scanner",
            "schedule": 300.0,
            "options": {"queue": "market"},
        },
        # 投资决策生成（每60秒）
        "investment-decision-generate": {
            "task": "app.tasks.decision.generate_investment_decisions",
            "schedule": 60.0,
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
        # MA均线交叉监控 (工作日15:30收盘后)
        "ma-cross-monitor": {
            "task": "app.tasks.ma_monitor.run_ma_monitor",
            "schedule": crontab(minute="30", hour="15", day_of_week="1-5"),
            "options": {"queue": "market"},
        },
        # 止损单检查（每30秒）
        "check-stop-orders": {
            "task": "app.tasks.auto_close.check_stop_orders",
            "schedule": 30.0,
            "options": {"queue": "market"},
        },
        # 超5日持仓自动平仓（工作日15:00收盘后）
        "close-expired-positions": {
            "task": "app.tasks.auto_close.close_expired_positions",
            "schedule": crontab(minute="0", hour="15", day_of_week="1-5"),
            "options": {"queue": "market"},
        },
    },
)
