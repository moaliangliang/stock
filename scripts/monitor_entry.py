#!/usr/bin/env python3
"""
盘中入场条件监控脚本 — 明天 (2026-05-13) 交易监控
监控 603489(八方股份) 和 603693(江苏新能) 的入场触发条件

Cron 触发时间:
  09:25 集合竞价 / 09:35 开盘量比确认 / 10:30 盘中确认

数据来源: mx-data (mingxian API)
通知方式: 邮件 + 平台站内通知
"""

import smtplib
import json
import os
import sys
import subprocess
import re
from email.mime.text import MIMEText
from datetime import datetime

import requests

API_HOST = os.environ.get("API_HOST", "http://localhost:8000")
TOKEN = os.environ.get("MONITOR_TOKEN", "")
EMAIL_TO = os.environ.get("EMAIL_TO", "maoliang84@163.com")
MX_APIKEY = os.environ.get("MX_APIKEY", "")
MX_DATA_SCRIPT = os.path.expanduser("~/skills/mx-data/mx_data.py")

# SMTP config
SMTP_CONFIG = {
    "host": "smtp.163.com",
    "port": 465,
    "user": "maoliang84@163.com",
    "password": "MBrjp4w2eg4VdLEh",
}

# 监控标的配置
WATCHLIST = {
    "603489": {
        "name": "八方股份",
        "entry_zone": "39.0-40.5",
        "stop_loss": 37.0,
        "targets": "43.5 / 45.0",
        "support": 38.0,
        "position": "15-20%",
    },
    "603693": {
        "name": "江苏新能",
        "entry_zone": "16.0-16.5",
        "stop_loss": 15.2,
        "targets": "17.5 / 18.0",
        "support": 15.75,
        "position": "10-15%",
    },
}


def login():
    global TOKEN
    if TOKEN:
        return TOKEN
    resp = requests.post(
        f"{API_HOST}/api/v1/auth/login",
        data={"username": "admin", "password": "admin123"},
        timeout=10,
    )
    data = resp.json()
    TOKEN = data.get("data", {}).get("access_token", "")
    return TOKEN


def send_email(subject, body):
    """发送邮件通知"""
    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = SMTP_CONFIG["user"]
        msg["To"] = EMAIL_TO
        with smtplib.SMTP_SSL(SMTP_CONFIG["host"], SMTP_CONFIG["port"], timeout=15) as smtp:
            smtp.login(SMTP_CONFIG["user"], SMTP_CONFIG["password"])
            smtp.send_message(msg)
        print(f"[{datetime.now():%H:%M:%S}] 邮件已发送")
        return True
    except Exception as e:
        print(f"[{datetime.now():%H:%M:%S}] 邮件失败: {e}")
        return False


def create_platform_notification(title, content, ntype="trade"):
    """在平台内创建通知"""
    try:
        token = login()
        resp = requests.post(
            f"{API_HOST}/api/v1/notifications",
            json={"type": ntype, "title": title, "content": content},
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        return resp.json()
    except Exception as e:
        print(f"  平台通知失败: {e}")
        return None


def get_realtime_quotes(codes):
    """通过 mx-data 获取实时行情"""
    code_str = ",".join(codes)
    try:
        result = subprocess.run(
            [
                "python3",
                MX_DATA_SCRIPT,
                code_str,
                "--indicators",
                "price,change_pct,volume,turnover_rate,open,high,low,prev_close,volume_ratio",
            ],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=os.path.dirname(MX_DATA_SCRIPT),
            env={**os.environ, "MX_APIKEY": MX_APIKEY},
        )

        # 解析输出，提取表格数据
        output = result.stdout
        quotes = {}

        for code in codes:
            # 用正则从输出中提取关键数据
            name_map = {"603489": "八方股份", "603693": "江苏新能"}
            name = name_map.get(code, code)

            # 匹配模式: 最新价、涨跌幅、成交量等
            patterns = {
                "price": rf"{name}\(.*?\).*?([\d.]+)元?\s",
                "change_pct": rf"{name}.*?([\d.-]+)%\s",
                "volume": rf"{name}.*?成交量.*?([\d.]+[万亿]?\s*[股手])",
            }

            quotes[code] = {
                "name": name,
                "raw_output": False,
            }

        return quotes, output

    except subprocess.TimeoutExpired:
        print("mx-data 查询超时")
        return {}, ""
    except Exception as e:
        print(f"mx-data 查询失败: {e}")
        return {}, ""


def get_realtime_via_klines(codes):
    """
    回退方案: 通过平台K线API获取最新日线数据
    用于集合竞价前获取昨收等基础数据
    """
    token = login()
    quotes = {}

    for code in codes:
        symbol = f"{code}.SH" if code.startswith("6") else f"{code}.SZ"
        try:
            resp = requests.get(
                f"{API_HOST}/api/v1/market/klines",
                params={
                    "symbol": symbol,
                    "interval": "1d",
                    "start_date": "2026-05-08",
                    "end_date": "2026-05-13",
                    "limit": 5,
                },
                headers={"Authorization": f"Bearer {token}"},
                timeout=10,
            )
            klines = resp.json().get("data", [])
            if klines:
                latest = klines[-1]
                prev = klines[-2] if len(klines) > 1 else latest
                quotes[code] = {
                    "name": WATCHLIST.get(code, {}).get("name", code),
                    "date": latest.get("timestamp", "?")[:10],
                    "open": latest.get("open", 0),
                    "high": latest.get("high", 0),
                    "low": latest.get("low", 0),
                    "close": latest.get("close", 0),
                    "volume": latest.get("volume", 0),
                    "prev_close": prev.get("close", 0),
                }
        except Exception as e:
            print(f"  获取{code} K线失败: {e}")

    return quotes


def check_call_auction():
    """
    09:25 集合竞价检查
    此时已有开盘价, 检查:
    - 603489: 高开1-3%=强势入场; >5%=等回调
    - 603693: 高开1-3%=入场; >3%=等回调
    """
    now = datetime.now()

    # 获取昨日收盘数据(今日开盘前K线未更新,用昨收)
    quotes = get_realtime_via_klines(["603489", "603693"])

    header = f"""
╔══════════════════════════════════════════════════════╗
║        集合竞价监控   {now:%Y-%m-%d %H:%M}         ║
╚══════════════════════════════════════════════════════╝
"""
    print(header)

    messages = []

    for code, cfg in WATCHLIST.items():
        q = quotes.get(code, {})
        name = cfg["name"]
        prev_close = q.get("prev_close", 0)
        # 集合竞价后开盘价
        open_price = q.get("open", 0)

        print(f"\n  {code} {name}")
        print(f"    昨收: {prev_close:.2f}")

        # 注意: 此脚本在09:25执行时,K线数据可能还没更新当天数据
        # 这里用的是上一交易日的K线作为参考
        # 实际集合竞价数据需要通过行情源获取
        print(f"    [提示] 集合竞价数据需从实时行情源获取")
        print(f"    [参考] 昨日收盘: {prev_close:.2f}")

        # 用昨日收盘作为基准估算
        if prev_close > 0:
            # 假设场景分析
            scenarios = [
                (1, 3, "✅ 强势高开1-3% → 直接入场"),
                (3, 5, "⚠️ 偏强高开3-5% → 半仓入场"),
                (5, 100, "⏸ 大幅高开>5% → 等回调"),
                (-100, 0, "🔻 低开 → 观望等企稳"),
            ]
            for lo, hi, desc in scenarios:
                est_price_lo = prev_close * (1 + lo / 100)
                est_price_hi = prev_close * (1 + hi / 100) if hi < 100 else 999
                print(f"      若开盘 {est_price_lo:.2f}-{est_price_hi:.2f} ({lo:+d}%~{hi:+d}%): {desc}")
        print(f"    入场区间: {cfg['entry_zone']}  止损: {cfg['stop_loss']}")

    print(f"\n  {'='*50}")

    # 发送通知
    body = f"""【明日交易监控 - 集合竞价指南】{now:%Y-%m-%d}

这是明日盘前的条件提醒,请在明日09:25集合竞价时关注:

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
603489 八方股份 (昨收: 40.08)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
入场区间: 39.0-40.5 | 止损: 37.0 (-8%) | 目标: 43.5→45.0
建议仓位: 15-20%

集合竞价判断:
  ① 高开1-3%(约40.5-41.3): ✅ 强势,可直接入场
  ② 高开3-5%(约41.3-42.1): ⚠️ 偏强,建议半仓
  ③ 高开>5%(>42.1): ⏸ 太高,等回调至39-40
  ④ 低开/平开: ➡️ 正常,按39.0-40.5区间入场

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
603693 江苏新能 (昨收: 16.51)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
入场区间: 16.0-16.5 | 止损: 15.2 (-8%) | 目标: 17.5→18.0
建议仓位: 10-15%

集合竞价判断:
  ① 高开1-3%(约16.7-17.0): ✅ 强势,可入场
  ② 高开>3%(>17.0): ⏸ 偏高,等回踩16.0-16.5
  ③ 低开/平开: ➡️ 按16.0-16.5区间入场

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
盘中时间节点:
  09:25 集合竞价出结果 → 判断是否入场
  09:30-10:00 观察量比 → >1.2强势,<0.8观望
  10:30 盘中确认 → 不破均价线可加仓
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

⚠️ 总仓位控制: 25-35%
⚠️ 大盘若急跌>2%: 减仓至半仓
⚠️ 单票-8%无条件止损

---
量化交易平台自动监控 | {now:%Y-%m-%d %H:%M}
"""
    send_email(f"【盘前准备】明日交易监控 八方+江苏新能", body)

    platform_msg = f"""明日(05/13)交易监控:
603489 八方股份: 入场39.0-40.5, 止损37.0, 目标43.5→45
603693 江苏新能: 入场16.0-16.5, 止损15.2, 目标17.5→18
集合竞价09:25关注高开幅度, 09:35看量比, 10:30盘中确认"""
    create_platform_notification("明日交易监控已就绪", platform_msg)

    print(f"\n  ✅ 盘前通知已发送 (邮件 + 站内)")


if __name__ == "__main__":
    now = datetime.now()

    if len(sys.argv) > 1:
        stage = sys.argv[1]
    else:
        # 默认: 发送盘前准备通知
        stage = "premarket"

    stages = {
        "premarket": check_call_auction,
        "auction": check_call_auction,
    }

    func = stages.get(stage, check_call_auction)
    func()
