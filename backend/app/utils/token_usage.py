"""
API Token 使用量统计 — 持久化 + 每月1日自动重置。

跨进程共享：通过 SQLite 数据库持久化，API server 和 Celery worker
共享同一份计数。每月1日首次访问时自动归零。
"""
import threading
from datetime import datetime
from typing import Dict

from app.core.database import sync_engine
from sqlalchemy import text

_lock = threading.Lock()

# 确保 stats 表存在
def _ensure_table():
    with sync_engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS api_usage_stats (
                api_name VARCHAR(50) PRIMARY KEY,
                call_count INTEGER DEFAULT 0,
                month_key VARCHAR(7) NOT NULL,
                monthly_limit INTEGER DEFAULT 300,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
        conn.commit()

_ensure_table()


def _current_month_key() -> str:
    return datetime.now().strftime("%Y-%m")


def _reset_if_new_month(conn, api_name: str, limit: int) -> str:
    """如果月份变了，重置计数。返回当前 month_key。"""
    month_key = _current_month_key()
    row = conn.execute(
        text("SELECT month_key FROM api_usage_stats WHERE api_name = :name"),
        {"name": api_name},
    ).fetchone()

    if row is None:
        conn.execute(
            text("INSERT INTO api_usage_stats (api_name, call_count, month_key, monthly_limit) VALUES (:name, 0, :mk, :lim)"),
            {"name": api_name, "mk": month_key, "lim": limit},
        )
    elif row[0] != month_key:
        conn.execute(
            text("UPDATE api_usage_stats SET call_count = 0, month_key = :mk, updated_at = CURRENT_TIMESTAMP WHERE api_name = :name"),
            {"name": api_name, "mk": month_key},
        )
    return month_key


def increment(api_name: str, limit: int = 300) -> int:
    """递增计数，返回递增后的 count。自动处理月初重置。"""
    with _lock:
        with sync_engine.connect() as conn:
            _reset_if_new_month(conn, api_name, limit)
            conn.execute(
                text("UPDATE api_usage_stats SET call_count = call_count + 1, updated_at = CURRENT_TIMESTAMP WHERE api_name = :name"),
                {"name": api_name},
            )
            conn.commit()
            row = conn.execute(
                text("SELECT call_count FROM api_usage_stats WHERE api_name = :name"),
                {"name": api_name},
            ).fetchone()
            return row[0] if row else 0


def check_quota(api_name: str, limit: int = 300) -> bool:
    """仅检查配额是否超限（不递增），超限返回 False。自动处理月初重置。"""
    with _lock:
        with sync_engine.connect() as conn:
            _reset_if_new_month(conn, api_name, limit)
            row = conn.execute(
                text("SELECT call_count, monthly_limit FROM api_usage_stats WHERE api_name = :name"),
                {"name": api_name},
            ).fetchone()

            count = row[0] if row else 0
            eff_limit = row[1] if row and row[1] else limit
            return count < eff_limit


def check_and_increment(api_name: str, limit: int = 300) -> bool:
    """检查配额并递增：未超限返回 True，超限返回 False。"""
    with _lock:
        with sync_engine.connect() as conn:
            _reset_if_new_month(conn, api_name, limit)
            row = conn.execute(
                text("SELECT call_count, monthly_limit FROM api_usage_stats WHERE api_name = :name"),
                {"name": api_name},
            ).fetchone()

            count = row[0] if row else 0
            eff_limit = row[1] if row and row[1] else limit

            if count >= eff_limit:
                return False

            conn.execute(
                text("UPDATE api_usage_stats SET call_count = call_count + 1, updated_at = CURRENT_TIMESTAMP WHERE api_name = :name"),
                {"name": api_name},
            )
            conn.commit()
            return True


def get_stats(api_name: str = None) -> Dict:
    """获取使用统计。不传 api_name 时返回所有 API 的统计。"""
    month_key = _current_month_key()
    with sync_engine.connect() as conn:
        if api_name:
            row = conn.execute(
                text("SELECT api_name, call_count, month_key, monthly_limit FROM api_usage_stats WHERE api_name = :name"),
                {"name": api_name},
            ).fetchone()
            if row is None:
                return {
                    "api_name": api_name,
                    "call_count": 0,
                    "monthly_limit": 300,
                    "month_key": month_key,
                    "reset_date": f"{month_key}-01",
                }
            # 月份变了但还没写入过 → 返回归零后的值
            db_month = row[2]
            return {
                "api_name": row[0],
                "call_count": row[1] if db_month == month_key else 0,
                "monthly_limit": row[3],
                "month_key": month_key,
                "reset_date": f"{month_key}-01",
            }

        rows = conn.execute(
            text("SELECT api_name, call_count, month_key, monthly_limit FROM api_usage_stats"),
        ).fetchall()
        return [
            {
                "api_name": r[0],
                "call_count": r[1] if r[2] == month_key else 0,
                "monthly_limit": r[3],
                "month_key": month_key,
                "reset_date": f"{month_key}-01",
            }
            for r in rows
        ]
