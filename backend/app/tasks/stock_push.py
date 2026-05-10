"""
股票推送定时任务 - 调用 stock-push.sh 脚本执行持仓监控与推送
"""
import os
import subprocess
from datetime import datetime, timezone

from loguru import logger

from app.core.celery_app import celery_app

SCRIPT_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "..", "scripts", "stock-push.sh")


@celery_app.task(queue="market")
def run_stock_push():
    """执行 stock-push.sh 持仓监控推送脚本"""
    script = os.path.abspath(SCRIPT_PATH)
    logger.info(f"执行 stock-push: {script} | {datetime.now(timezone.utc)}")
    try:
        # 传递 UTF-8 环境变量，确保中文不乱码
        env = os.environ.copy()
        env.setdefault("LANG", "zh_CN.UTF-8")
        env.setdefault("LC_ALL", "zh_CN.UTF-8")
        env.setdefault("PYTHONIOENCODING", "utf-8")
        result = subprocess.run(
            ["bash", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            timeout=30,
        )
        if result.returncode == 0:
            if result.stdout.strip():
                logger.info(f"stock-push 完成:\n{result.stdout.strip()}")
        else:
            logger.error(f"stock-push 失败 (exit={result.returncode}):\n{result.stderr}")
    except subprocess.TimeoutExpired:
        logger.error("stock-push 执行超时")
    except Exception as e:
        logger.error(f"stock-push 异常: {e}")
