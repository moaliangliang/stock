"""
MA均线交叉监控任务 — 每日盘后检查沪深300/创业板/中证500的MA交叉信号
金叉/死叉时通过Bark推送通知

各指数最优策略:
  沪深300: MA(120,250)
  创业板指数: MA(120,250)
  中证500: MA(60,250)
"""
import json
import logging
import os
from datetime import datetime, timezone, timedelta

import requests
from sqlalchemy import text

from app.core.celery_app import celery_app
from app.core.config import settings
from app.core.database import SyncSessionLocal
from app.core.redis import TaskLock

logger = logging.getLogger(__name__)

BARK_URL = "https://api.day.app/push"

# 状态文件路径(记录每个指数+策略的上一状态)
STATE_FILE = os.path.join(os.path.dirname(__file__), "..", "..", "ma_monitor_state.json")

# 监控配置: (symbol, name, short_ma, long_ma)
WATCHLIST = [
    ("000300.SH", "沪深300", 120, 250),
    ("399006.SZ", "创业板", 120, 250),
    ("000905.SH", "中证500", 60, 250),
    (".IXIC", "纳斯达克", 120, 250),
]


def push_bark(title: str, body: str, group: str = "ma_monitor") -> bool:
    """Bark推送通知"""
    if not settings.BARK_KEY:
        logger.info(f"BARK_KEY未配置, 跳过推送: {title}")
        return False

    payload = {
        "device_key": settings.BARK_KEY,
        "title": title,
        "body": body,
        "badge": 1,
        "sound": "default",
        "group": group,
    }
    try:
        resp = requests.post(BARK_URL, json=payload, timeout=10)
        if resp.status_code == 200:
            logger.info(f"MA监控推送成功: {title}")
            return True
        else:
            logger.warning(f"MA监控推送失败: {resp.status_code} {resp.text}")
            return False
    except Exception as e:
        logger.error(f"MA监控推送异常: {e}")
        return False


def _load_state() -> dict:
    """加载上次记录的交叉状态"""
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save_state(state: dict):
    """保存交叉状态"""
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"保存MA状态文件失败: {e}")


def _calc_ma(closes: list[float], period: int) -> list:
    """计算均线, 返回与closes等长列表, 前period-1个为None"""
    n = len(closes)
    ma = [None] * n
    if n < period:
        return ma
    win = sum(closes[:period])
    ma[period - 1] = win / period
    for i in range(period, n):
        win += closes[i] - closes[i - period]
        ma[i] = win / period
    return ma


def _detect_cross(ma_s: list, ma_l: list, dates: list, lookback_days: int = 5):
    """
    检测最近lookback_days天内是否有交叉
    返回: (cross_type, cross_date, cross_price, current_state)
    """
    n = len(ma_s)
    if n == 0:
        return None, None, None, None

    current_state = "above" if ma_s[-1] > ma_l[-1] else "below"

    # 从lookback窗口的起点扫描, 正确初始化prev_state
    check_from = max(0, n - lookback_days - 2)

    prev_state = None
    for i in range(check_from, n):
        if ma_s[i] is None or ma_l[i] is None:
            continue
        cur = "above" if ma_s[i] > ma_l[i] else "below"
        if prev_state is None:
            prev_state = cur
            continue
        if prev_state != cur:
            cross_type = "golden" if cur == "above" else "death"
            return cross_type, dates[i], ma_s[i], current_state
        prev_state = cur

    return None, None, None, current_state


@celery_app.task(queue="market")
def run_ma_monitor():
    """
    每日盘后(15:30)检查三大指数的MA交叉信号。
    仅在金叉/死叉发生时推送通知。
    每日也推送一个简洁的当前状态。
    """
    with TaskLock("run_ma_monitor", timeout=600) as acquired:
        if not acquired:
            return "Skipped: another instance is running"

        logger.info(f"MA均线监控开始: {datetime.now(timezone.utc)}")

        db = SyncSessionLocal()
        try:
            state = _load_state()
            alerts = []
            status_lines = []

            for sym, name, short_p, long_p in WATCHLIST:
                key = f"{sym}_{short_p}_{long_p}"

                # 查询K线数据
                rows = db.execute(
                    text(
                        "SELECT timestamp, close FROM kline_data "
                        "WHERE symbol=:sym AND interval='1d' ORDER BY timestamp"
                    ),
                    {"sym": sym},
                ).fetchall()

                if not rows:
                    continue

                closes = [float(r[1]) for r in rows]
                dates = [r[0] for r in rows]

                # 解析日期
                parsed_dates = []
                for d in dates:
                    if isinstance(d, str):
                        from datetime import datetime as dt
                        parsed_dates.append(dt.strptime(d[:19], "%Y-%m-%d %H:%M:%S"))
                    else:
                        parsed_dates.append(d)

                # 计算均线
                ma_s = _calc_ma(closes, short_p)
                ma_l = _calc_ma(closes, long_p)

                # 检测交叉
                cross_type, cross_date, cross_price, current_state = _detect_cross(
                    ma_s, ma_l, parsed_dates, lookback_days=5
                )

                ma_s_val = ma_s[-1]
                ma_l_val = ma_l[-1]
                close_val = closes[-1]
                diff_pct = (ma_s_val - ma_l_val) / ma_l_val * 100 if ma_l_val else 0

                # 状态行
                emoji = "🟢" if current_state == "above" else "🔴"
                status_lines.append(
                    f"{emoji} {name} MA({short_p},{long_p}): "
                    f"收盘{close_val:.0f} | MA{short_p}={ma_s_val:.0f} | MA{long_p}={ma_l_val:.0f} | 差值{diff_pct:+.1f}%"
                )

                # 检查是否有新的交叉
                prev_state = state.get(key)

                if cross_type:
                    cross_date_str = cross_date.strftime("%Y-%m-%d") if cross_date else "?"

                    if cross_type == "golden" and prev_state != "above":
                        alerts.append(
                            f"🔔 {name} MA({short_p},{long_p}) 金叉!\n"
                            f"日期: {cross_date_str}\n"
                            f"价格: {cross_price:.2f}\n"
                            f"短均线上穿长均线 → 买入信号"
                        )

                    elif cross_type == "death" and prev_state != "below":
                        alerts.append(
                            f"⚠️ {name} MA({short_p},{long_p}) 死叉!\n"
                            f"日期: {cross_date_str}\n"
                            f"价格: {cross_price:.2f}\n"
                            f"短均线下穿长均线 → 卖出信号"
                        )

                # 更新状态
                state[key] = current_state

            # 保存状态
            _save_state(state)

            # 推送交叉警报(仅在有新交叉时)
            if alerts:
                for alert in alerts:
                    push_bark("📊 MA交叉信号", alert, group="ma_cross")
                    logger.info(f"MA交叉警报: {alert[:80]}...")

                # 也存数据库
                notif_content = "\n\n".join(alerts)
                try:
                    from app.models.notification import Notification
                    notif = Notification(
                        user_id=1,
                        type="trade",
                        title="MA均线交叉信号",
                        content=notif_content,
                        metadata_json={"alerts": alerts},
                    )
                    db.add(notif)
                    db.commit()
                except Exception as e:
                    logger.error(f"保存通知到DB失败: {e}")

            # 每日状态摘要(仅在交易日推送, 避免周末无意义推送)
            today = datetime.now(timezone.utc)
            # 中国时间
            cn_hour = (today.hour + 8) % 24
            cn_weekday = today.weekday()  # 0=Monday, 6=Sunday

            # 只在周一到周五推送每日摘要
            if cn_weekday < 5:
                summary = "\n".join(status_lines)
                push_bark(f"📈 MA均线日报 ({today.strftime('%m/%d')})", summary, group="ma_daily")
                logger.info(f"MA日报已推送")

            logger.info(f"MA均线监控完成: 无新交叉" if not alerts else f"MA均线监控完成: {len(alerts)}个交叉")

        except Exception as e:
            db.rollback()
            logger.error(f"MA监控异常: {e}", exc_info=True)
        finally:
            db.close()
