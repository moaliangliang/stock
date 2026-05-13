#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
明日交易监控与准备脚本
用途: 盘前/收盘后自动运行，生成次日交易计划并推送通知

功能:
  1. 获取持仓标的 + 监控列表的技术指标
  2. 多策略信号检查 (MA趋势 / KDJ金叉 / 布林 / 网格)
  3. 生成明日操作建议报告
  4. 推送 Bark + 邮件通知
  5. --afternoon 模式: 13:00 对比上午数据，给出下午操作建议

用法:
  python monitor_tomorrow.py                     # 默认: 全部检查 + 生成报告
  python monitor_tomorrow.py --afternoon         # 午后模式: 上午复盘 + 下午建议
  python monitor_tomorrow.py --no-notify         # 只生成报告，不推送
  python monitor_tomorrow.py --output /path      # 指定输出目录
"""

import os
import sys
import json
import urllib.request
import re
import smtplib
from email.mime.text import MIMEText
from datetime import datetime, date, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict

# ─── Config ───────────────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "..", ".openclaw", "workspace", "mx_data", "output")
OUTPUT_DIR = os.path.abspath(OUTPUT_DIR)
STATE_FILE = os.path.join(SCRIPT_DIR, "monitor_tomorrow_state.json")

# 加载 backend/.env
ENV_FILE = os.path.join(SCRIPT_DIR, "backend", ".env")
if os.path.exists(ENV_FILE):
    with open(ENV_FILE) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                key, val = key.strip(), val.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = val

MX_APIKEY = os.environ.get("MX_APIKEY", "")
MX_URL = "https://mkapi2.dfcfs.com/finskillshub/api/claw/query"

# Bark push
BARK_KEY = os.environ.get("BARK_KEY", "")
BARK_URL = "https://api.day.app/push"

# SMTP (163)
SMTP_CONFIG = {
    "host": "smtp.163.com",
    "port": 465,
    "user": "maoliang84@163.com",
    "password": os.environ.get("SMTP_PASS", ""),
}
EMAIL_TO = os.environ.get("EMAIL_TO", "maoliang84@163.com")

# Sina realtime quotes (no auth needed)
SINA_QUOTE_URL = "https://hq.sinajs.cn/list={codes}"
SINA_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://finance.sina.com.cn/",
}

# Afternoon report output
AFTERNOON_STATE_FILE = os.path.join(SCRIPT_DIR, "monitor_afternoon_state.json")
MORNING_SNAPSHOT_FILE = os.path.join(SCRIPT_DIR, "monitor_morning_snapshot.json")

# ─── Stock Watchlist ──────────────────────────────────────────
# (query_name, display_name, code, strategy, checker_type)
STOCK_CONFIGS = [
    # ── 自定义策略 ──
    ("立讯精密 002475", "立讯精密", "002475.SZ", "trend",        "custom_lixun"),
    ("金风科技 002202", "金风科技", "002202.SZ", "kdj_pullback", "custom_jinfeng"),
    ("巴比食品 605338", "巴比食品", "605338.SH", "bottom_fish",  "custom_babi"),
    # ── MA趋势 ──
    ("东山精密 002384", "东山精密", "002384.SZ", "ma_cross",   "generic_ma_trend"),
    ("中际旭创 300308", "中际旭创", "300308.SZ", "ma_cross",   "generic_ma_trend"),
    ("寒武纪 688256",   "寒武纪",   "688256.SH", "ma_cross",   "generic_ma_trend"),
    # ── KDJ金叉 ──
    ("生益科技 600183", "生益科技", "600183.SH", "kdj",        "generic_kdj"),
    ("沪电股份 002463", "沪电股份", "002463.SZ", "kdj",        "generic_kdj"),
    ("浪潮信息 000977", "浪潮信息", "000977.SZ", "kdj",        "generic_kdj"),
    ("澜起科技 688008", "澜起科技", "688008.SH", "kdj",        "generic_kdj"),
    # ── 网格/布林 ──
    ("亨通光电 600487", "亨通光电", "600487.SH", "grid",       "generic_grid"),
    ("深南电路 002916", "深南电路", "002916.SZ", "bollinger",  "generic_boll_grid"),
    # ── 额外关注 ──
    ("八方股份 603489", "八方股份", "603489.SH", "kdj",        "generic_kdj"),
    ("江苏新能 603693", "江苏新能", "603693.SH", "kdj",        "generic_kdj"),
]


# ─── Helpers ──────────────────────────────────────────────────
def safe_float(val, default=0.0):
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def fetch_technical_data(stock_names):
    """批量获取技术指标"""
    query = " ".join(stock_names) + " MACD KDJ 均线 布林带 最新价格 成交量 RSI"
    headers = {"Content-Type": "application/json", "apikey": MX_APIKEY}
    data = {"toolQuery": query}
    try:
        req = urllib.request.Request(MX_URL, data=json.dumps(data).encode(), headers=headers)
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except Exception as e:
        print(f"[ERROR] API request failed: {e}", file=sys.stderr)
        return None


def parse_indicators(raw_data):
    """解析 API 返回的原始数据为结构化指标"""
    try:
        tables = raw_data["data"]["data"]["searchDataResultDTO"]["dataTableDTOList"]
    except (TypeError, KeyError):
        return {}
    result = {}
    for t in tables:
        entity = t.get("entityName", "")
        if "H5162" in entity:
            continue
        symbol = entity.split("(")[1].split(")")[0] if "(" in entity else entity
        name_map = t.get("nameMap", {})
        raw_table = t.get("rawTable", {})
        if not raw_table.get("headName"):
            continue
        name_vals = list(name_map.values())
        has_close = any("收盘价" in v for v in name_vals)
        has_macd = any("MACD" in v for v in name_vals)
        if has_close:
            table_type = "price"
        elif has_macd:
            table_type = "technical"
        else:
            continue
        if symbol not in result:
            result[symbol] = {"technical": {}, "price": {}}
        for code, values in raw_table.items():
            if code in ("headName", "headNameSub"):
                continue
            readable = name_map.get(code, code)
            if not readable or readable == code:
                continue
            latest = values[0] if isinstance(values, list) and values else values
            history = values[:5] if isinstance(values, list) else [values]
            result[symbol][table_type][readable] = {"latest": latest, "history": history}
    return result


# ─── Strategy Checkers ───────────────────────────────────────

def check_lixun(indicators):
    tech = indicators.get("002475.SZ", {}).get("technical", {})
    price_data = indicators.get("002475.SZ", {}).get("price", {})
    signals = []
    close = safe_float(price_data.get("收盘价", {}).get("latest", 0))
    ma5 = safe_float(tech.get("5日MA简单移动平均", {}).get("latest", 0))
    ma20 = safe_float(tech.get("20日MA简单移动平均", {}).get("latest", 0))
    ma60 = safe_float(tech.get("60日MA简单移动平均", {}).get("latest", 0))
    diff = safe_float(tech.get("MACD指数平滑异同平均-DIFF", {}).get("latest", 0))
    dea = safe_float(tech.get("MACD指数平滑异同平均-DEA", {}).get("latest", 0))
    k = safe_float(tech.get("KDJ(K值)", {}).get("latest", 0))
    d = safe_float(tech.get("KDJ(D值)", {}).get("latest", 0))
    j = safe_float(tech.get("KDJ(J值)", {}).get("latest", 0))
    boll_up = safe_float(tech.get("BOLL布林线UP", {}).get("latest", 0))
    if close == 0:
        return signals
    macd_bullish = diff > dea
    ma_aligned = ma5 > ma20 > ma60 and close > ma5
    kdj_neutral = 20 < k < 80 and 20 < d < 80
    if macd_bullish and ma_aligned and kdj_neutral:
        signals.append(f"🟢 [立讯精密] 主升浪买入 - MACD多头+均线多头+KDJ中性 | 现价{close:.2f}")
    elif macd_bullish and ma_aligned:
        if j > 100:
            signals.append(f"⚠️ [立讯精密] 趋势多头但KDJ超买(J={j:.0f})，等回调 | 现价{close:.2f}")
    if close <= ma20 * 1.02 and macd_bullish:
        signals.append(f"🟢 [立讯精密] 回调至20日线({ma20:.2f})+MACD未死叉 | 现价{close:.2f} 加仓")
    if diff < dea and close < ma20:
        signals.append(f"🔴 [立讯精密] MACD死叉+跌破20日线 | 现价{close:.2f} 减仓/清仓")
    if close < ma60:
        signals.append(f"🔴 [立讯精密] 跌破60日线({ma60:.2f}) | 现价{close:.2f} 清仓离场")
    return signals


def check_jinfeng(indicators):
    tech = indicators.get("002202.SZ", {}).get("technical", {})
    price_data = indicators.get("002202.SZ", {}).get("price", {})
    signals = []
    close = safe_float(price_data.get("收盘价", {}).get("latest", 0))
    ma5 = safe_float(tech.get("5日MA简单移动平均", {}).get("latest", 0))
    ma20 = safe_float(tech.get("20日MA简单移动平均", {}).get("latest", 0))
    ma60 = safe_float(tech.get("60日MA简单移动平均", {}).get("latest", 0))
    diff = safe_float(tech.get("MACD指数平滑异同平均-DIFF", {}).get("latest", 0))
    dea = safe_float(tech.get("MACD指数平滑异同平均-DEA", {}).get("latest", 0))
    k = safe_float(tech.get("KDJ(K值)", {}).get("latest", 0))
    d = safe_float(tech.get("KDJ(D值)", {}).get("latest", 0))
    j = safe_float(tech.get("KDJ(J值)", {}).get("latest", 0))
    if close == 0:
        return signals
    kdj_overbought = j > 90 or k > 80
    if kdj_overbought:
        signals.append(f"🔴 [金风科技] KDJ超买(J={j:.0f},K={k:.0f}) | 现价{close:.2f} 等回调")
    elif j < 50 and k < 50 and diff > dea:
        signals.append(f"🟢 [金风科技] KDJ回调到位(J={j:.0f})+MACD多头 | 现价{close:.2f} 建仓")
    if close <= ma20 * 1.05 and diff > dea and not kdj_overbought:
        signals.append(f"🟢 [金风科技] 回踩20日线({ma20:.2f})+MACD金叉 | 现价{close:.2f} 加仓")
    if diff > dea and close > ma5 and 30 < j < 70:
        signals.append(f"🟢 [金风科技] MACD金叉+站上5日线+KDJ修复 | 现价{close:.2f}")
    if diff < dea and close < ma60:
        signals.append(f"🔴 [金风科技] MACD死叉+跌破60日线 | 现价{close:.2f} 止损")
    return signals


def check_babi(indicators):
    tech = indicators.get("605338.SH", {}).get("technical", {})
    price_data = indicators.get("605338.SH", {}).get("price", {})
    signals = []
    close = safe_float(price_data.get("收盘价", {}).get("latest", 0))
    volume = safe_float(price_data.get("成交量", {}).get("latest", 0))
    ma5 = safe_float(tech.get("5日MA简单移动平均", {}).get("latest", 0))
    ma20 = safe_float(tech.get("20日MA简单移动平均", {}).get("latest", 0))
    diff = safe_float(tech.get("MACD指数平滑异同平均-DIFF", {}).get("latest", 0))
    dea = safe_float(tech.get("MACD指数平滑异同平均-DEA", {}).get("latest", 0))
    k = safe_float(tech.get("KDJ(K值)", {}).get("latest", 0))
    d = safe_float(tech.get("KDJ(D值)", {}).get("latest", 0))
    j = safe_float(tech.get("KDJ(J值)", {}).get("latest", 0))
    boll_low = safe_float(tech.get("BOLL布林线LOW", {}).get("latest", 0))
    rsi = safe_float(tech.get("RSI相对强弱指标", {}).get("latest", 0))
    if close == 0:
        return signals
    if boll_low > 0 and close <= boll_low * 1.02:
        signals.append(f"🟢 [巴比食品] 触及BOLL下轨({boll_low:.2f}) | 现价{close:.2f} 网格底仓")
    if close > ma5 and volume > 2500000:
        signals.append(f"🟢 [巴比食品] 站上5日线+放量 | 现价{close:.2f} 右侧确认")
    if close > ma20 and diff > dea:
        signals.append(f"🟢 [巴比食品] 突破20日线+MACD金叉 | 现价{close:.2f} 趋势逆转")
    if close < boll_low * 0.97:
        signals.append(f"🔴 [巴比食品] 跌破BOLL下轨({boll_low:.2f}) | 现价{close:.2f} 止损")
    return signals


def check_ma_trend(code, name, indicators):
    tech = indicators.get(code, {}).get("technical", {})
    price_data = indicators.get(code, {}).get("price", {})
    signals = []
    close = safe_float(price_data.get("收盘价", {}).get("latest", 0))
    ma5 = safe_float(tech.get("5日MA简单移动平均", {}).get("latest", 0))
    ma20 = safe_float(tech.get("20日MA简单移动平均", {}).get("latest", 0))
    ma60 = safe_float(tech.get("60日MA简单移动平均", {}).get("latest", 0))
    diff = safe_float(tech.get("MACD指数平滑异同平均-DIFF", {}).get("latest", 0))
    dea = safe_float(tech.get("MACD指数平滑异同平均-DEA", {}).get("latest", 0))
    k = safe_float(tech.get("KDJ(K值)", {}).get("latest", 0))
    d = safe_float(tech.get("KDJ(D值)", {}).get("latest", 0))
    j = safe_float(tech.get("KDJ(J值)", {}).get("latest", 0))
    boll_up = safe_float(tech.get("BOLL布林线UP", {}).get("latest", 0))
    if close == 0:
        return signals
    p = f"[{name}]"
    macd_bullish = diff > dea
    ma_aligned = ma5 > ma20 > ma60 and close > ma5
    kdj_neutral = 20 < k < 80 and 20 < d < 80
    if macd_bullish and ma_aligned and kdj_neutral:
        signals.append(f"🟢 {p} 主升浪买入 | 现价{close:.2f}")
    elif macd_bullish and ma_aligned and j > 100:
        signals.append(f"⚠️ {p} 趋势多头但KDJ超买(J={j:.0f}) | 现价{close:.2f}")
    if close <= ma20 * 1.02 and macd_bullish:
        signals.append(f"🟢 {p} 回调20日线+MACD未死叉 | 现价{close:.2f}")
    if j < 25 and k < 30 and diff > dea:
        signals.append(f"🟢 {p} KDJ超卖反弹+MACD多头 | 现价{close:.2f}")
    if diff < dea and close < ma20:
        signals.append(f"🔴 {p} MACD死叉+跌破20日线 | 现价{close:.2f}")
    if close < ma60:
        signals.append(f"🔴 {p} 跌破60日线({ma60:.2f}) | 现价{close:.2f}")
    return signals


def check_kdj_generic(code, name, indicators):
    tech = indicators.get(code, {}).get("technical", {})
    price_data = indicators.get(code, {}).get("price", {})
    signals = []
    close = safe_float(price_data.get("收盘价", {}).get("latest", 0))
    ma5 = safe_float(tech.get("5日MA简单移动平均", {}).get("latest", 0))
    ma20 = safe_float(tech.get("20日MA简单移动平均", {}).get("latest", 0))
    ma60 = safe_float(tech.get("60日MA简单移动平均", {}).get("latest", 0))
    diff = safe_float(tech.get("MACD指数平滑异同平均-DIFF", {}).get("latest", 0))
    dea = safe_float(tech.get("MACD指数平滑异同平均-DEA", {}).get("latest", 0))
    k = safe_float(tech.get("KDJ(K值)", {}).get("latest", 0))
    d = safe_float(tech.get("KDJ(D值)", {}).get("latest", 0))
    j = safe_float(tech.get("KDJ(J值)", {}).get("latest", 0))
    if close == 0:
        return signals
    p = f"[{name}]"
    kdj_golden = k > d
    if kdj_golden and 30 < k < 70 and close > ma5:
        signals.append(f"🟢 {p} KDJ金叉+站上5日线 | 现价{close:.2f}")
    elif kdj_golden and k < 30:
        signals.append(f"🟢 {p} KDJ超卖区金叉 | 现价{close:.2f}")
    if j < 25 and diff > dea:
        signals.append(f"🟢 {p} KDJ超卖+MACD多头 | 现价{close:.2f}")
    if kdj_golden and close > ma20 and diff > dea:
        signals.append(f"🟢 {p} KDJ金叉+站上20日线+MACD多头 | 现价{close:.2f} 加仓")
    if j > 90 or k > 80:
        signals.append(f"🔴 {p} KDJ超买(J={j:.0f}) | 现价{close:.2f} 等回调")
    if diff < dea and close < ma20:
        signals.append(f"🔴 {p} MACD死叉+跌破20日线 | 现价{close:.2f}")
    if close < ma60:
        signals.append(f"🔴 {p} 跌破60日线({ma60:.2f}) | 现价{close:.2f}")
    return signals


def check_grid_generic(code, name, indicators):
    tech = indicators.get(code, {}).get("technical", {})
    price_data = indicators.get(code, {}).get("price", {})
    signals = []
    close = safe_float(price_data.get("收盘价", {}).get("latest", 0))
    ma20 = safe_float(tech.get("20日MA简单移动平均", {}).get("latest", 0))
    ma60 = safe_float(tech.get("60日MA简单移动平均", {}).get("latest", 0))
    diff = safe_float(tech.get("MACD指数平滑异同平均-DIFF", {}).get("latest", 0))
    dea = safe_float(tech.get("MACD指数平滑异同平均-DEA", {}).get("latest", 0))
    k = safe_float(tech.get("KDJ(K值)", {}).get("latest", 0))
    j = safe_float(tech.get("KDJ(J值)", {}).get("latest", 0))
    boll_low = safe_float(tech.get("BOLL布林线LOW", {}).get("latest", 0))
    boll_mid = safe_float(tech.get("BOLL布林线", {}).get("latest", 0))
    boll_up = safe_float(tech.get("BOLL布林线UP", {}).get("latest", 0))
    if close == 0:
        return signals
    p = f"[{name}]"
    if boll_low > 0 and close <= boll_low * 1.03:
        signals.append(f"🟢 {p} 触及BOLL下轨({boll_low:.2f}) | 现价{close:.2f} 网格底仓")
    elif boll_mid > 0 and close <= boll_mid * 1.02:
        signals.append(f"🟡 {p} 回调BOLL中轨({boll_mid:.2f}) | 现价{close:.2f}")
    if j < 25:
        signals.append(f"🟢 {p} KDJ超卖(J={j:.0f}) | 现价{close:.2f} 网格买入")
    if close < ma20 and j < 40 and diff > dea:
        signals.append(f"🟢 {p} 20日线下+KDJ低位+MACD多头 | 现价{close:.2f}")
    if boll_up > 0 and close > boll_up * 0.98:
        signals.append(f"📊 {p} 接近BOLL上轨({boll_up:.2f}) | 现价{close:.2f} 止盈区")
    if diff < dea and close < ma60:
        signals.append(f"🔴 {p} MACD死叉+跌破60日线 | 现价{close:.2f}")
    return signals


def check_boll_grid(code, name, indicators):
    tech = indicators.get(code, {}).get("technical", {})
    price_data = indicators.get(code, {}).get("price", {})
    signals = []
    close = safe_float(price_data.get("收盘价", {}).get("latest", 0))
    ma5 = safe_float(tech.get("5日MA简单移动平均", {}).get("latest", 0))
    ma20 = safe_float(tech.get("20日MA简单移动平均", {}).get("latest", 0))
    ma60 = safe_float(tech.get("60日MA简单移动平均", {}).get("latest", 0))
    diff = safe_float(tech.get("MACD指数平滑异同平均-DIFF", {}).get("latest", 0))
    dea = safe_float(tech.get("MACD指数平滑异同平均-DEA", {}).get("latest", 0))
    k = safe_float(tech.get("KDJ(K值)", {}).get("latest", 0))
    j = safe_float(tech.get("KDJ(J值)", {}).get("latest", 0))
    rsi = safe_float(tech.get("RSI相对强弱指标", {}).get("latest", 0))
    boll_low = safe_float(tech.get("BOLL布林线LOW", {}).get("latest", 0))
    boll_mid = safe_float(tech.get("BOLL布林线", {}).get("latest", 0))
    boll_up = safe_float(tech.get("BOLL布林线UP", {}).get("latest", 0))
    if close == 0:
        return signals
    p = f"[{name}]"
    if boll_low > 0 and close <= boll_low * 1.02:
        signals.append(f"🟢 {p} 触及BOLL下轨({boll_low:.2f}) | 现价{close:.2f} 布林买入")
    elif boll_mid > 0 and close <= boll_mid * 1.01 and diff > dea:
        signals.append(f"🟢 {p} 回调BOLL中轨+MACD多头 | 现价{close:.2f}")
    if boll_up > 0 and close > boll_up * 0.95 and diff > dea and 50 < k < 85:
        signals.append(f"🟢 {p} 布林开口向上+MACD多头 | 现价{close:.2f}")
    if close < ma5 and close > ma20 and j < 40 and diff > dea:
        signals.append(f"🟢 {p} 回踩5日线+KDJ修复 | 现价{close:.2f}")
    if rsi > 0 and rsi < 30:
        signals.append(f"📊 {p} RSI超卖({rsi:.1f}) | 现价{close:.2f}")
    if boll_up > 0 and close > boll_up * 1.02:
        signals.append(f"🔴 {p} 突破BOLL上轨({boll_up:.2f}) | 现价{close:.2f} 超买减仓")
    if diff < dea and close < ma20:
        signals.append(f"🔴 {p} MACD死叉+跌破20日线 | 现价{close:.2f}")
    if close < ma60:
        signals.append(f"🔴 {p} 跌破60日线({ma60:.2f}) | 现价{close:.2f}")
    return signals


# ─── Notification ────────────────────────────────────────────

def push_bark(title, body, group="monitor_tomorrow"):
    if not BARK_KEY:
        return False
    try:
        req = urllib.request.Request(
            BARK_URL,
            data=json.dumps({"device_key": BARK_KEY, "title": title, "body": body, "group": group}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception as e:
        print(f"[Bark] 推送失败: {e}", file=sys.stderr)
        return False


def send_email(subject, body):
    if not SMTP_CONFIG["password"]:
        print("[Email] SMTP_PASS not set, skip", file=sys.stderr)
        return False
    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = SMTP_CONFIG["user"]
        msg["To"] = EMAIL_TO
        import smtplib as _smtplib
        with _smtplib.SMTP_SSL(SMTP_CONFIG["host"], SMTP_CONFIG["port"], timeout=15) as smtp:
            smtp.login(SMTP_CONFIG["user"], SMTP_CONFIG["password"])
            smtp.send_message(msg)
        return True
    except Exception as e:
        print(f"[Email] 发送失败: {e}", file=sys.stderr)
        return False


# ─── Afternoon Monitoring ────────────────────────────────────

def _symbol_to_sina(symbol):
    """Convert 002475.SZ → sz002475, 600183.SH → sh600183"""
    code = symbol.replace(".SZ", "").replace(".SH", "").replace(".BJ", "")
    if ".SZ" in symbol:
        return f"sz{code}"
    elif ".SH" in symbol:
        return f"sh{code}"
    elif ".BJ" in symbol:
        return f"bj{code}"
    return None


def _sina_to_symbol(sina_code):
    """Convert sh600183 → 600183.SH"""
    if sina_code.startswith("sh"):
        return f"{sina_code[2:]}.SH"
    elif sina_code.startswith("sz"):
        return f"{sina_code[2:]}.SZ"
    elif sina_code.startswith("bj"):
        return f"{sina_code[2:]}.BJ"
    return sina_code


def fetch_realtime_quotes(symbols):
    """批量获取新浪实时行情 (无认证, 免费)
    Returns: {symbol: {name, open, prev_close, last_price, high, low, volume, amount, change_pct}}
    """
    mapping = {}
    for sym in symbols:
        sina = _symbol_to_sina(sym)
        if sina:
            mapping[sina] = sym
    if not mapping:
        return {}

    url = SINA_QUOTE_URL.format(codes=",".join(mapping.keys()))
    try:
        req = urllib.request.Request(url, headers=SINA_HEADERS)
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("gbk")
    except Exception as e:
        print(f"[Sina] 行情请求失败: {e}", file=sys.stderr)
        return {}

    result = {}
    for line in raw.strip().split("\n"):
        if not line.strip():
            continue
        m = re.match(r'var hq_str_(\w+)="(.*)"', line.strip())
        if not m:
            continue
        sina_code, values = m.group(1), m.group(2)
        sym = _sina_to_symbol(sina_code)
        if sym not in symbols:
            continue
        parts = values.split(",")
        if len(parts) < 32:
            continue
        try:
            last_price = float(parts[3]) if parts[3] else 0
            prev_close = float(parts[2]) if parts[2] else 0
            result[sym] = {
                "name": parts[0],
                "open": float(parts[1]) if parts[1] else 0,
                "prev_close": prev_close,
                "last_price": last_price,
                "high": float(parts[4]) if parts[4] else 0,
                "low": float(parts[5]) if parts[5] else 0,
                "volume": float(parts[8]) if parts[8] else 0,
                "amount": float(parts[9]) if parts[9] else 0,
                "change_pct": round((last_price - prev_close) / prev_close * 100, 2) if prev_close else 0,
                "bid": float(parts[6]) if parts[6] else 0,
                "ask": float(parts[7]) if parts[7] else 0,
                "date": parts[30] if len(parts) > 30 else "",
                "time": parts[31] if len(parts) > 31 else "",
            }
        except (ValueError, IndexError):
            continue
    return result


def analyze_afternoon(quotes, prev_state=None):
    """根据上午行情(quotes) + 前日技术面(prev_state) 生成下午操作建议

    决策规则:
      1. 上午涨 + 午后在均价线上方 → 持有/加仓
      2. 上午涨 + 午后跌破均价线 → 减仓
      3. 上午跌 + 反弹不过昨收 → 继续观望
      4. 上午跌 + 午后放量突破上午高点 → 反转确认可追
    """
    afternoon_signals = []

    for cfg in STOCK_CONFIGS:
        name, code, strategy = cfg[1], cfg[2], cfg[3]
        q = quotes.get(code)
        if not q or q["last_price"] == 0:
            continue

        price = q["last_price"]
        open_price = q["open"]
        prev_close = q["prev_close"]
        high = q["high"]
        low = q["low"]
        change_pct = q["change_pct"]

        # 估算上午均价线 (没有真实VWAP，用 (open+high+low+price)/4 近似)
        est_vwap = (open_price + high + low + price) / 4
        morning_up = (price > open_price * 1.005) or (price > prev_close and open_price <= price)

        # 上午箱体
        morning_range_pct = (high - low) / prev_close * 100 if prev_close else 0
        # 当前价在日内范围的位置 (0=最低, 1=最高)
        if high > low:
            range_position = (price - low) / (high - low)
        else:
            range_position = 0.5

        # 从昨日状态文件获取前日技术面作为参考
        prev_stock = {}
        if prev_state:
            prev_stock = prev_state.get("stocks", {}).get(code, {})

        sigs = []

        # ── 规则1: 上午涨 + 午后在均价线上方 ──
        if morning_up and price > est_vwap:
            if range_position > 0.7:
                sigs.append(f"🟢 [{name}] 上午强势+午后站稳均价线上方 | 现价{price:.2f}(+{change_pct}%) 持有/加仓")
            else:
                sigs.append(f"🟢 [{name}] 上午偏强+均价线上方 | 现价{price:.2f} 继续持有 尾盘不破可加")

        # ── 规则2: 上午涨 + 午后跌破均价线 ──
        if morning_up and price < est_vwap:
            sigs.append(f"🔴 [{name}] 上午曾涨但午后跌破均价线({est_vwap:.2f}) | 现价{price:.2f} 减仓锁定利润")

        # ── 规则3: 上午跌 + 反弹不过昨收 ──
        afternoon_drop = price < open_price * 0.995 and change_pct < -0.5
        if afternoon_drop and price < prev_close:
            sigs.append(f"🔴 [{name}] 上午偏弱+未能站上昨收({prev_close:.2f}) | 现价{price:.2f}({change_pct}%) 继续观望不抄底")

        # ── 规则4: 上午跌 + 午后放量突破上午高点 ──
        if afternoon_drop and price > high * 0.995 and price > open_price:
            sigs.append(f"📊 [{name}] 午后回升接近上午高点({high:.2f}) | 现价{price:.2f} 关注能否放量突破 突破可追")

        # ── 补充规则 ──

        # 午后急拉 >5% 追高风险
        if change_pct > 5:
            sigs.append(f"⚠️ [{name}] 涨幅已达{change_pct}% | 现价{price:.2f} 不追高 等次日回调")

        # 午后急跌 >5% 接近BOLL下轨/支撑位机会
        if change_pct < -5:
            sigs.append(f"📊 [{name}] 跌幅{change_pct}% 午后急跌 | 现价{price:.2f} 观察14:30能否企稳 企稳可轻仓抄底")

        # 窄幅震荡 无方向
        if abs(change_pct) < 1 and morning_range_pct < 2:
            sigs.append(f"➡️ [{name}] 上午窄幅震荡(振幅{morning_range_pct:.1f}%) | 现价{price:.2f} 等待方向选择 尾盘定方向后操作")

        # 尾盘放量拉升预警 (14:30后)
        now = datetime.now()
        if now.hour >= 14 and now.minute >= 30 and price > est_vwap and change_pct > 1:
            sigs.append(f"📊 [{name}] 尾盘放量拉升 | 现价{price:.2f} 若为真突破次日大概率高开 可持有过夜")

        # 尾盘砸盘预警
        if now.hour >= 14 and now.minute >= 30 and price < est_vwap and change_pct < -1:
            sigs.append(f"🔴 [{name}] 尾盘跳水 | 现价{price:.2f} 次日大概率低开 建议减仓")

        afternoon_signals.extend(sigs)

        # 无信号时给个状态
        if not sigs:
            dir_str = "偏强" if morning_up else "偏弱"
            afternoon_signals.append(f"➡️ [{name}] 上午{dir_str} | 现价{price:.2f}({change_pct:+.2f}%) 振幅{morning_range_pct:.1f}% | 继续按原计划执行")

    return afternoon_signals


def generate_afternoon_report(quotes, afternoon_signals, now):
    """生成午后操作建议 Markdown 报告"""
    buy_signals = [s for s in afternoon_signals if s.startswith("🟢")]
    sell_signals = [s for s in afternoon_signals if s.startswith("🔴")]
    warn_signals = [s for s in afternoon_signals if s.startswith("⚠️")]
    info_signals = [s for s in afternoon_signals if s.startswith("📊") or s.startswith("➡️")]

    lines = []
    lines.append(f"# 午后操作建议 — {now.strftime('%Y-%m-%d')}")
    lines.append(f"> 生成时间: {now.strftime('%H:%M')} | 上午收盘 11:30 → 午后开盘 13:00")
    lines.append("")

    # ── 大盘概览 ──
    lines.append("## 上午盘面总结")
    lines.append("")
    up_count = sum(1 for q in quotes.values() if q["change_pct"] > 0)
    down_count = sum(1 for q in quotes.values() if q["change_pct"] < 0)
    lines.append(f"- 监控 {len(quotes)} 只标的: {up_count}涨 / {down_count}跌")
    lines.append("")

    # ── 操作建议 ──
    lines.append("## 午后操作建议")
    lines.append("")

    # 午后节奏提醒
    lines.append("### 时间节点")
    lines.append("| 时段 | 特征 | 策略 |")
    lines.append("|------|------|------|")
    lines.append("| 13:00-13:30 | 消化午间消息，波动最大 | 不追涨杀跌，等企稳 |")
    lines.append("| 13:30-14:30 | 平稳期，方向延续 | 上午强势+回踩均价不破→加仓 |")
    lines.append("| 14:30-15:00 | 尾盘，主力真实意图 | 放量拉尾=次日高开；砸尾=次日低开 |")
    lines.append("")

    if buy_signals:
        lines.append("### 持有/加仓")
        for s in buy_signals:
            lines.append(f"- {s}")
        lines.append("")

    if sell_signals:
        lines.append("### 减仓/清仓")
        for s in sell_signals:
            lines.append(f"- {s}")
        lines.append("")

    if warn_signals:
        lines.append("### 风险提示")
        for s in warn_signals:
            lines.append(f"- {s}")
        lines.append("")

    if info_signals:
        lines.append("### 关注/持有")
        for s in info_signals:
            lines.append(f"- {s}")
        lines.append("")

    # ── 上午数据明细 ──
    lines.append("## 上午数据明细")
    lines.append("")
    lines.append("| 标的 | 现价 | 涨幅% | 开盘 | 最高 | 最低 | 上午方向 | 均价线 | 建议 |")
    lines.append("|------|------|-------|------|------|------|----------|--------|------|")
    for cfg in STOCK_CONFIGS:
        name, code = cfg[1], cfg[2]
        q = quotes.get(code, {})
        if not q:
            continue
        price = q["last_price"]
        chg = q["change_pct"]
        open_p = q["open"]
        high_p = q["high"]
        low_p = q["low"]
        est_vwap = (open_p + high_p + low_p + price) / 4

        if price > open_p * 1.005:
            direction = "⬆️ 强势"
        elif price < open_p * 0.995:
            direction = "⬇️ 弱势"
        else:
            direction = "➡️ 震荡"

        # Find this stock's primary afternoon signal
        stock_sig = "—"
        for s in afternoon_signals:
            if name in s:
                if s.startswith("🟢"):
                    stock_sig = "持有/加仓"
                elif s.startswith("🔴"):
                    stock_sig = "减仓/观望"
                elif s.startswith("⚠️"):
                    stock_sig = "追高风险"
                elif s.startswith("📊"):
                    stock_sig = "关注确认"
                elif s.startswith("➡️"):
                    stock_sig = "按计划"
                break

        lines.append(f"| {name} | {price:.2f} | {chg:+.2f} | {open_p:.2f} | {high_p:.2f} | {low_p:.2f} | {direction} | {est_vwap:.2f} | {stock_sig} |")
    lines.append("")

    # ── 决策参考 ──
    lines.append("## 午后决策速查表")
    lines.append("")
    lines.append("| 上午走势 | 午后站上均价线 | 午后跌破均价线 |")
    lines.append("|----------|:-------------:|:-------------:|")
    lines.append("| **上午涨** | 🟢 持有/加仓 | 🔴 减仓 |")
    lines.append("| **上午跌** | 📊 等尾盘确认 | 🔴 观望不抄底 |")
    lines.append("| **窄幅震荡** | ➡️ 等方向选择 | ➡️ 等方向选择 |")
    lines.append("")

    report = "\n".join(lines)
    return report


def run_afternoon(args):
    """午后模式主流程"""
    if not MX_APIKEY:
        print("[ERROR] MX_APIKEY not set", file=sys.stderr)
        sys.exit(1)

    now = datetime.now()
    symbols = [cfg[2] for cfg in STOCK_CONFIGS]

    print(f"\n{'='*60}")
    print(f"  午后操作监控 — {now.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  上午收盘 11:30 → 午后开盘 13:00 复盘")
    print(f"{'='*60}")

    # 1. 获取实时行情 (新浪, 免费无需认证)
    print(f"\n📡 获取 {len(symbols)} 只标的实时行情 (新浪) ...")
    quotes = fetch_realtime_quotes(symbols)

    if not quotes:
        print("[ERROR] 无法获取实时行情")
        sys.exit(1)

    print(f"  获取到 {len(quotes)} 只标的实时数据")

    # 2. 加载前日状态 (用于参考MA/MACD等)
    prev_state = None
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                prev_state = json.load(f)
        except (json.JSONDecodeError, KeyError):
            pass

    # 3. 保存上午快照 (用于下午后续对比)
    morning_snapshot = {
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M:%S"),
        "quotes": {k: {kk: vv for kk, vv in v.items() if kk != "name"} for k, v in quotes.items()},
    }
    os.makedirs(os.path.dirname(MORNING_SNAPSHOT_FILE), exist_ok=True)
    with open(MORNING_SNAPSHOT_FILE, "w") as f:
        json.dump(morning_snapshot, f, ensure_ascii=False, indent=2)

    # 4. 分析下午操作
    print("\n📊 分析上午数据，生成下午建议 ...")
    afternoon_signals = analyze_afternoon(quotes, prev_state)

    # 5. 输出信号
    buy_signals = [s for s in afternoon_signals if s.startswith("🟢")]
    sell_signals = [s for s in afternoon_signals if s.startswith("🔴")]
    warn_signals = [s for s in afternoon_signals if s.startswith("⚠️")]

    print(f"\n📢 午后信号 (加仓:{len(buy_signals)} 减仓:{len(sell_signals)} 警告:{len(warn_signals)})")
    print("-" * 50)
    for s in afternoon_signals:
        print(f"  {s}")
    print("-" * 50)

    # 6. 个股上午快照
    print(f"\n{'─'*60}")
    for cfg in STOCK_CONFIGS:
        name, code = cfg[1], cfg[2]
        q = quotes.get(code, {})
        if not q:
            print(f"  {name}({code}) 无数据")
            continue
        est_vwap = (q["open"] + q["high"] + q["low"] + q["last_price"]) / 4
        print(f"  {name}({code}) | 现价:{q['last_price']:.2f}({q['change_pct']:+.2f}%) | 昨收:{q['prev_close']:.2f} | 开盘:{q['open']:.2f} | 高{high:.2f} 低{low:.2f} | 估均价:{est_vwap:.2f}")
    print(f"{'─'*60}\n")

    # 7. 生成午后报告
    report = generate_afternoon_report(quotes, afternoon_signals, now)
    os.makedirs(args.output, exist_ok=True)
    date_str = now.strftime("%Y%m%d")
    report_path = os.path.join(args.output, f"午后操作建议_{date_str}.md")
    with open(report_path, "w") as f:
        f.write(report)
    print(f"📄 午后报告: {report_path}")

    # 8. 保存午后状态
    stocks_state = {}
    for cfg in STOCK_CONFIGS:
        name, code = cfg[1], cfg[2]
        q = quotes.get(code, {})
        if q:
            stocks_state[code] = {
                "name": name,
                "strategy": cfg[3],
                "last_price": q["last_price"],
                "open": q["open"],
                "high": q["high"],
                "low": q["low"],
                "prev_close": q["prev_close"],
                "change_pct": q["change_pct"],
            }
    state = {
        "updated": now.strftime("%Y-%m-%d %H:%M:%S"),
        "stocks": stocks_state,
        "buy_signals": buy_signals,
        "sell_signals": sell_signals,
    }
    with open(AFTERNOON_STATE_FILE, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

    # 9. 推送通知 (精简版, 行动信号优先)
    if not args.no_notify:
        urgent = buy_signals + sell_signals + warn_signals
        if urgent:
            bark_lines = [f"📊 午后操作 {now:%m-%d %H:%M}"]
            bark_lines.append(f"加仓:{len(buy_signals)} 减仓:{len(sell_signals)}")
            bark_lines.extend(urgent[:6])  # 最多6条
            bark_body = "\n".join(bark_lines)
        else:
            bark_body = f"午后无紧急操作信号 | {len(quotes)}只标的正常 | 按原计划执行"

        push_bark(f"📊 午后操作 {now:%m-%d %H:%M}", bark_body)

        email_body = f"【午后操作建议】{now:%Y-%m-%d %H:%M}\n\n{bark_body}\n\n详见: {report_path}"
        send_email(f"午后操作建议 {now:%m-%d}", email_body)
        print("📨 通知已推送 (Bark + Email)")


# ─── Report Generator ────────────────────────────────────────

def generate_report(all_indicators, all_signals, now):
    """生成明日操作建议 Markdown 报告"""
    buy_signals = [s for s in all_signals if s.startswith("🟢")]
    warn_signals = [s for s in all_signals if s.startswith("⚠️")]
    sell_signals = [s for s in all_signals if s.startswith("🔴")]
    info_signals = [s for s in all_signals if s.startswith("📊") or s.startswith("🟡")]

    # 按标的汇总
    by_stock = defaultdict(list)
    for s in all_signals:
        for cfg in STOCK_CONFIGS:
            if cfg[1] in s:
                by_stock[cfg[1]].append(s)
                break

    lines = []
    lines.append(f"# 明日操作建议 — {now.strftime('%Y-%m-%d')}")
    lines.append("")
    lines.append(f"> 生成时间: {now.strftime('%Y-%m-%d %H:%M')} | 监控标的: {len(STOCK_CONFIGS)}只")
    lines.append("")

    # ── 紧急操作 ──
    lines.append("## 明日操作清单")
    lines.append("")
    if buy_signals:
        lines.append("### 买入信号")
        for s in buy_signals:
            lines.append(f"- {s}")
    else:
        lines.append("### 买入信号")
        lines.append("- 暂无明确买入信号，等待盘中确认")
    lines.append("")

    if sell_signals:
        lines.append("### 减仓/止损信号")
        for s in sell_signals:
            lines.append(f"- {s}")
        lines.append("")

    # ── 标的技术面概览 ──
    lines.append("## 标的技术面概览")
    lines.append("")
    lines.append("| 标的 | 代码 | 策略 | 现价 | MA5 | MA20 | K | D | J | MACD | 状态 |")
    lines.append("|------|------|------|------|-----|------|---|---|---|------|------|")
    for cfg in STOCK_CONFIGS:
        name, code = cfg[1], cfg[2]
        data = all_indicators.get(code, {})
        tech = data.get("technical", {})
        price_data = data.get("price", {})
        close = safe_float(price_data.get("收盘价", {}).get("latest", 0))
        ma5 = safe_float(tech.get("5日MA简单移动平均", {}).get("latest", 0))
        ma20 = safe_float(tech.get("20日MA简单移动平均", {}).get("latest", 0))
        k = safe_float(tech.get("KDJ(K值)", {}).get("latest", 0))
        d = safe_float(tech.get("KDJ(D值)", {}).get("latest", 0))
        j = safe_float(tech.get("KDJ(J值)", {}).get("latest", 0))
        diff = safe_float(tech.get("MACD指数平滑异同平均-DIFF", {}).get("latest", 0))
        dea = safe_float(tech.get("MACD指数平滑异同平均-DEA", {}).get("latest", 0))
        macd_str = "多头" if diff > dea else "空头"
        # 综合状态
        stock_signals = by_stock.get(name, [])
        has_buy = any("🟢" in s for s in stock_signals)
        has_sell = any("🔴" in s for s in stock_signals)
        has_warn = any("⚠️" in s for s in stock_signals)
        if has_buy:
            status = "🟢 买入"
        elif has_sell:
            status = "🔴 卖出"
        elif has_warn:
            status = "⚠️ 关注"
        else:
            status = "➡️ 持有"
        lines.append(f"| {name} | {code} | {cfg[3]} | {close:.2f} | {ma5:.2f} | {ma20:.2f} | {k:.1f} | {d:.1f} | {j:.1f} | {macd_str} | {status} |")
    lines.append("")

    # ── 风险提示 ──
    lines.append("## 风险提示")
    lines.append("")
    lines.append("- 单票仓位上限: 20%，总仓位上限: 70%")
    lines.append("- 单票止损线: -8%，无条件执行")
    lines.append("- 大盘急跌>2%: 总仓位减半")
    lines.append("- 关注盘中量比变化，集合竞价09:25确认")
    lines.append("")

    report = "\n".join(lines)
    return report


# ─── Main ────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="明日交易监控与准备")
    parser.add_argument("--afternoon", action="store_true", help="午后模式: 13:00复盘上午+给出下午建议")
    parser.add_argument("--no-notify", action="store_true", help="只生成报告，不推送通知")
    parser.add_argument("--output", type=str, default=OUTPUT_DIR, help="报告输出目录")
    args = parser.parse_args()

    if args.afternoon:
        return run_afternoon(args)

    # ── 以下是盘前/收盘模式 (默认) ──

    if not MX_APIKEY:
        print("[ERROR] MX_APIKEY not set", file=sys.stderr)
        sys.exit(1)

    now = datetime.now()
    print(f"\n{'='*60}")
    print(f"  明日交易监控 — {now.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}")

    # 1. 并行获取所有标的技术数据
    all_indicators = {}
    query_names = [s[0] for s in STOCK_CONFIGS]
    batch_size = 4
    batches = [query_names[i:i + batch_size] for i in range(0, len(query_names), batch_size)]

    print(f"\n📡 获取 {len(query_names)} 只标的技术数据 ...")
    with ThreadPoolExecutor(max_workers=len(batches)) as executor:
        futures = {executor.submit(fetch_technical_data, batch): batch for batch in batches}
        for future in as_completed(futures):
            try:
                raw = future.result()
                if raw:
                    indicators = parse_indicators(raw)
                    all_indicators.update(indicators)
            except Exception as e:
                print(f"[WARN] API batch failed: {e}", file=sys.stderr)

    if not all_indicators:
        print("[ERROR] 无法获取任何数据")
        sys.exit(1)

    # 2. 执行所有策略检查
    CHECKER_MAP = {
        "custom_lixun":      lambda: check_lixun(all_indicators),
        "custom_jinfeng":    lambda: check_jinfeng(all_indicators),
        "custom_babi":       lambda: check_babi(all_indicators),
        "generic_ma_trend":  lambda: check_ma_trend(cfg[2], cfg[1], all_indicators),
        "generic_kdj":       lambda: check_kdj_generic(cfg[2], cfg[1], all_indicators),
        "generic_grid":      lambda: check_grid_generic(cfg[2], cfg[1], all_indicators),
        "generic_boll_grid": lambda: check_boll_grid(cfg[2], cfg[1], all_indicators),
    }

    all_signals = []
    for cfg in STOCK_CONFIGS:
        checker_type = cfg[4]
        checker = CHECKER_MAP.get(checker_type)
        if checker:
            all_signals.extend(checker())

    # 3. 输出信号
    buy_signals = [s for s in all_signals if s.startswith("🟢")]
    sell_signals = [s for s in all_signals if s.startswith("🔴")]
    warn_signals = [s for s in all_signals if s.startswith("⚠️")]

    if all_signals:
        print(f"\n📢 信号汇总 (买入:{len(buy_signals)} 卖出:{len(sell_signals)} 警告:{len(warn_signals)})")
        print("-" * 50)
        for s in all_signals:
            print(f"  {s}")
        print("-" * 50)
    else:
        print("\n✅ 所有标的无异常信号")

    # 4. 个股快照
    print(f"\n{'─'*60}")
    for cfg in STOCK_CONFIGS:
        name, code = cfg[1], cfg[2]
        data = all_indicators.get(code, {})
        tech = data.get("technical", {})
        price_data = data.get("price", {})
        close = price_data.get("收盘价", {}).get("latest", "N/A")
        k_val = tech.get("KDJ(K值)", {}).get("latest", "N/A")
        j_val = tech.get("KDJ(J值)", {}).get("latest", "N/A")
        diff = tech.get("MACD指数平滑异同平均-DIFF", {}).get("latest", "N/A")
        dea = tech.get("MACD指数平滑异同平均-DEA", {}).get("latest", "N/A")
        print(f"  {name}({code}) 策略:{cfg[3]} | 收盘:{close} | KDJ-K:{k_val} J:{j_val} | MACD DIFF:{diff} DEA:{dea}")
    print(f"{'─'*60}\n")

    # 5. 生成报告
    report = generate_report(all_indicators, all_signals, now)
    os.makedirs(args.output, exist_ok=True)
    date_str = now.strftime("%Y%m%d")
    report_path = os.path.join(args.output, f"明日操作建议_{date_str}.md")
    with open(report_path, "w") as f:
        f.write(report)
    print(f"📄 报告已保存: {report_path}")

    # 6. 保存状态文件
    stocks_state = {}
    for cfg in STOCK_CONFIGS:
        name, code = cfg[1], cfg[2]
        data = all_indicators.get(code, {})
        tech = data.get("technical", {})
        price_data = data.get("price", {})
        stocks_state[code] = {
            "name": name,
            "strategy": cfg[3],
            "close": safe_float(price_data.get("收盘价", {}).get("latest", 0)),
            "ma5": safe_float(tech.get("5日MA简单移动平均", {}).get("latest", 0)),
            "ma20": safe_float(tech.get("20日MA简单移动平均", {}).get("latest", 0)),
            "ma60": safe_float(tech.get("60日MA简单移动平均", {}).get("latest", 0)),
            "macd_diff": safe_float(tech.get("MACD指数平滑异同平均-DIFF", {}).get("latest", 0)),
            "macd_dea": safe_float(tech.get("MACD指数平滑异同平均-DEA", {}).get("latest", 0)),
            "kdj_k": safe_float(tech.get("KDJ(K值)", {}).get("latest", 0)),
            "kdj_d": safe_float(tech.get("KDJ(D值)", {}).get("latest", 0)),
            "kdj_j": safe_float(tech.get("KDJ(J值)", {}).get("latest", 0)),
        }
    state = {
        "updated": now.strftime("%Y-%m-%d %H:%M:%S"),
        "stocks": stocks_state,
        "buy_signals": buy_signals,
        "sell_signals": sell_signals,
    }
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

    # 7. 推送通知
    if not args.no_notify:
        summary_lines = [f"明日操作建议 {now:%m-%d}"]
        if buy_signals:
            summary_lines.append(f"买入信号: {len(buy_signals)}个")
            summary_lines.extend(buy_signals[:5])
        if sell_signals:
            summary_lines.append(f"卖出信号: {len(sell_signals)}个")
            summary_lines.extend(sell_signals[:3])
        if not buy_signals and not sell_signals:
            summary_lines.append("无明确操作信号，继续持有")

        bark_body = "\n".join(summary_lines)
        push_bark(f"📊 明日操作 {now:%m-%d %H:%M}", bark_body)

        email_body = f"【明日操作建议】{now:%Y-%m-%d}\n\n{bark_body}\n\n详见: {report_path}"
        send_email(f"明日操作建议 {now:%m-%d}", email_body)
        print("📨 通知已推送 (Bark + Email)")


if __name__ == "__main__":
    main()
