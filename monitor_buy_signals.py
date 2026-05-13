#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
12支回测精选股票买入信号监控脚本
策略类型: trend(主升浪), kdj_pullback(KDJ回调), bottom_fish(抄底),
          ma_cross(MA趋势), kdj(KDJ金叉), grid(网格), bollinger(布林)

v2: 集成回测门禁 + J值过热过滤
    - 买入信号须通过历史回测验证（年化>5% 且 盈亏比>1.2）
    - KDJ J>90 / K>80 时降级买入信号为警告
"""

import os
import sys
import json
import sqlite3
import urllib.request
from datetime import datetime, time
from concurrent.futures import ThreadPoolExecutor, as_completed

# Config
ALERT_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'buy_signal_alerts.log')
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'buy_signal_state.json')

# Load env vars from .env file if present
ENV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'backend', '.env')
if os.path.exists(ENV_FILE):
    with open(ENV_FILE) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, _, val = line.partition('=')
                key, val = key.strip(), val.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = val

# Stock configuration: (query_name, display_name, code, strategy, checker_type)
STOCK_CONFIGS = [
    # ——— 自定义策略（已有精细逻辑）———
    ("立讯精密 002475", "立讯精密", "002475.SZ", "trend",        "custom_lixun"),
    ("金风科技 002202", "金风科技", "002202.SZ", "kdj_pullback", "custom_jinfeng"),
    ("巴比食品 605338", "巴比食品", "605338.SH", "bottom_fish",  "custom_babi"),
    ("特变电工 600089", "特变电工", "600089.SH", "trend",        "custom_tebian"),
    # ——— MA趋势策略（回测最佳：MA_CROSS）———
    ("东山精密 002384", "东山精密", "002384.SZ", "ma_cross",   "generic_ma_trend"),
    ("中际旭创 300308", "中际旭创", "300308.SZ", "ma_cross",   "generic_ma_trend"),
    ("寒武纪 688256",   "寒武纪",   "688256.SH", "ma_cross",   "generic_ma_trend"),
    # ——— KDJ金叉策略（回测最佳：KDJ）———
    ("生益科技 600183", "生益科技", "600183.SH", "kdj",        "generic_kdj"),
    ("沪电股份 002463", "沪电股份", "002463.SZ", "kdj",        "generic_kdj"),
    ("浪潮信息 000977", "浪潮信息", "000977.SZ", "kdj",        "generic_kdj"),
    ("澜起科技 688008", "澜起科技", "688008.SH", "kdj",        "generic_kdj"),
    # ——— 网格/布林策略 ———
    ("亨通光电 600487", "亨通光电", "600487.SH", "grid",       "generic_grid"),
    ("深南电路 002916", "深南电路", "002916.SZ", "bollinger",  "generic_boll_grid"),
]

# ── 回测门禁缓存（避免每次运行都查DB/跑回测）──
_BACKTEST_CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'backtest_gate_cache.json')
_BACKTEST_CACHE: dict = {}
_CACHE_TTL = 86400  # 24小时有效

# 策略类型 → 回测策略名映射
_STRATEGY_TO_BACKTEST_TYPE = {
    "ma_cross": "ma_cross",
    "kdj": "kdj",
    "trend": "ma_cross",
    "kdj_pullback": "ma_cross",
    "bottom_fish": "kdj",
    "grid": "grid",
    "bollinger": "bollinger",
}


def _load_backtest_cache():
    global _BACKTEST_CACHE
    if os.path.exists(_BACKTEST_CACHE_FILE):
        try:
            with open(_BACKTEST_CACHE_FILE) as f:
                _BACKTEST_CACHE = json.load(f)
        except (json.JSONDecodeError, IOError):
            _BACKTEST_CACHE = {}


def _save_backtest_cache():
    with open(_BACKTEST_CACHE_FILE, 'w') as f:
        json.dump(_BACKTEST_CACHE, f, ensure_ascii=False, indent=2)


def validate_strategy_backtest(code: str, strategy: str) -> dict:
    """
    回测门禁：验证指定股票+策略历史上是否赚钱。
    优先从DB backtest_results表查询，无结果时从缓存读取。

    Returns: {"passed": bool, "annual_return": float, "profit_factor": float, "reason": str}
    """
    bt_type = _STRATEGY_TO_BACKTEST_TYPE.get(strategy, strategy)
    cache_key = f"{code}_{bt_type}"

    # Check cache
    if cache_key in _BACKTEST_CACHE:
        entry = _BACKTEST_CACHE[cache_key]
        age = datetime.now().timestamp() - entry.get('ts', 0)
        if age < _CACHE_TTL:
            return entry['result']

    result = {"passed": False, "annual_return": 0, "profit_factor": 0, "reason": ""}

    # Try SQLite DB
    db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'backend', 'quant_trade.db')
    if os.path.exists(db_path):
        try:
            conn = sqlite3.connect(db_path)
            # Strip .SH/.SZ suffix for DB query (DB may have either format)
            sym = code.replace('.SH', '').replace('.SZ', '')
            rows = conn.execute(
                "SELECT annual_return, profit_factor, total_trades, win_rate "
                "FROM backtest_results "
                "WHERE (symbol = ? OR symbol = ? OR symbol = ?) AND strategy_type = ? "
                "ORDER BY annual_return DESC LIMIT 1",
                (code, code + '.SH', code + '.SZ', bt_type)
            ).fetchall()
            conn.close()

            if rows:
                ar, pf, tr, wr = rows[0]
                ar_pct = (ar or 0) * 100
                pf_val = pf or 0
                tr_val = tr or 0

                if ar_pct > 5 and pf_val > 1.2 and tr_val >= 3:
                    result = {"passed": True, "annual_return": ar_pct, "profit_factor": pf_val,
                             "total_trades": tr_val, "win_rate": (wr or 0) * 100,
                             "reason": f"回测通过: 年化{ar_pct:.0f}% PF{pf_val:.2f}"}
                elif rows:
                    result = {"passed": False, "annual_return": ar_pct, "profit_factor": pf_val,
                             "total_trades": tr_val, "win_rate": (wr or 0) * 100,
                             "reason": f"回测不达标: 年化{ar_pct:.0f}%(需>5%) PF{pf_val:.2f}(需>1.2)"}
        except Exception as e:
            result["reason"] = f"DB查询异常: {e}"

    if not result["reason"]:
        result["reason"] = "无回测记录，需先运行回测"

    # Cache
    _BACKTEST_CACHE[cache_key] = {"ts": datetime.now().timestamp(), "result": result}
    return result


def apply_backtest_gate(signals: list, code: str, strategy: str) -> list:
    """
    对买入信号应用回测门禁：不通过回测的信号降级或剔除。
    """
    gate = validate_strategy_backtest(code, strategy)
    filtered = []
    for s in signals:
        if s.startswith('🟢'):
            if gate['passed']:
                filtered.append(s + f" [{gate['reason']}]")
            else:
                # 降级：回测不过的买入信号变成⚠️观察信号
                filtered.append(s.replace('🟢', '⚠️ [回测未过]', 1) + f" | {gate['reason']}")
        else:
            filtered.append(s)
    return filtered

# mx-data API
MX_APIKEY = os.environ.get("MX_APIKEY", "")
MX_URL = "https://mkapi2.dfcfs.com/finskillshub/api/claw/query"

# Bark push notification
BARK_KEY = os.environ.get("BARK_KEY", "")
BARK_URL = "https://api.day.app/push"

def fetch_technical_data(stock_names):
    """Fetch latest technical indicators for given stocks"""
    query = " ".join(stock_names) + " MACD KDJ 均线 布林带 最新价格 成交量 RSI"

    headers = {
        "Content-Type": "application/json",
        "apikey": MX_APIKEY
    }
    data = {"toolQuery": query}

    try:
        req = urllib.request.Request(MX_URL, data=json.dumps(data).encode(), headers=headers)
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except Exception as e:
        print(f"[ERROR] API request failed: {e}", file=sys.stderr)
        return None


def safe_float(val, default=0.0):
    """Safely convert value to float, handling '-' and 'N/A' placeholders"""
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def parse_indicators(raw_data):
    """Parse raw API response into structured indicator dict"""
    try:
        tables = raw_data['data']['data']['searchDataResultDTO']['dataTableDTOList']
    except (TypeError, KeyError):
        return {}
    result = {}

    for t in tables:
        entity = t.get('entityName', '')
        if 'H5162' in entity:  # Skip HK shares
            continue

        symbol = entity.split('(')[1].split(')')[0] if '(' in entity else entity
        name_map = t.get('nameMap', {})
        raw_table = t.get('rawTable', {})
        dates = raw_table.get('headName', [])

        if not dates:
            continue

        # Determine table type: price table has 收盘价, technical has MACD/KDJ
        name_vals = list(name_map.values())
        has_close = any('收盘价' in v for v in name_vals)
        has_macd = any('MACD' in v for v in name_vals)

        if has_close:
            table_type = 'price'
        elif has_macd:
            table_type = 'technical'
        else:
            continue  # Skip unknown tables

        if symbol not in result:
            result[symbol] = {'technical': {}, 'price': {}}

        # Map all indicators using name_map
        for code, values in raw_table.items():
            if code in ('headName', 'headNameSub'):
                continue
            readable = name_map.get(code, code)
            if not readable or readable == code:
                continue  # skip unmapped codes
            latest = values[0] if isinstance(values, list) and values else values
            history = values[:5] if isinstance(values, list) else [values]
            result[symbol][table_type][readable] = {'latest': latest, 'history': history}

    return result


def check_lixun(indicators):
    """Check 立讯精密(002475.SZ) buy conditions"""
    tech = indicators.get('002475.SZ', {}).get('technical', {})
    price_data = indicators.get('002475.SZ', {}).get('price', {})

    signals = []

    # Get values
    close = safe_float(price_data.get('收盘价', {}).get('latest', 0))
    ma5 = safe_float(tech.get('5日MA简单移动平均', {}).get('latest', 0))
    ma20 = safe_float(tech.get('20日MA简单移动平均', {}).get('latest', 0))
    ma60 = safe_float(tech.get('60日MA简单移动平均', {}).get('latest', 0))
    diff = safe_float(tech.get('MACD指数平滑异同平均-DIFF', {}).get('latest', 0))
    dea = safe_float(tech.get('MACD指数平滑异同平均-DEA', {}).get('latest', 0))
    k = safe_float(tech.get('KDJ(K值)', {}).get('latest', 0))
    d = safe_float(tech.get('KDJ(D值)', {}).get('latest', 0))
    j = safe_float(tech.get('KDJ(J值)', {}).get('latest', 0))
    boll_mid = safe_float(tech.get('BOLL布林线', {}).get('latest', 0))
    boll_up = safe_float(tech.get('BOLL布林线UP', {}).get('latest', 0))

    if close == 0:
        return signals

    desc = f"立讯精密 现价{close:.2f}"

    # Primary entry: MACD bullish + MA aligned + KDJ neutral
    macd_bullish = diff > dea
    ma_aligned = ma5 > ma20 > ma60 and close > ma5
    kdj_neutral = 20 < k < 80 and 20 < d < 80

    if macd_bullish and ma_aligned and kdj_neutral:
        signals.append(f"🟢 [立讯精密] 主升浪买入 - MACD多头+均线多头+KDJ中性 | 现价{close:.2f}")
    elif macd_bullish and ma_aligned:
        if j > 100:
            signals.append(f"⚠️ [立讯精密] 趋势多头但KDJ超买(J={j:.0f})，等回调不追高 | 现价{close:.2f}")
        elif k > 80:
            signals.append(f"🟡 [立讯精密] 趋势多头但KDJ高位(K={k:.0f})，轻仓 | 现价{close:.2f}")

    # Second entry: pullback to 20MA
    pullback_20ma = close <= ma20 * 1.02  # within 2% of 20MA
    if pullback_20ma and macd_bullish:
        signals.append(f"🟢 [立讯精密] 回调至20日均线({ma20:.2f})附近+MACD未死叉 | 现价{close:.2f} 第二批加仓")

    # MACD death cross warning
    if diff < dea and close < ma20:
        signals.append(f"🔴 [立讯精密] MACD死叉+跌破20日线 | 现价{close:.2f} 建议减仓/清仓")

    # Price breaking BOLL upper with volume
    if boll_up > 0 and close > boll_up * 0.98:
        signals.append(f"📊 [立讯精密] 价格接近BOLL上轨({boll_up:.2f}) | 现价{close:.2f} 注意压力")

    # Stop loss: below 60MA
    if close < ma60:
        signals.append(f"🔴 [立讯精密] 跌破60日均线({ma60:.2f}) | 现价{close:.2f} 清仓离场")

    return signals


def check_jinfeng(indicators):
    """Check 金风科技(002202.SZ) buy conditions"""
    tech = indicators.get('002202.SZ', {}).get('technical', {})
    price_data = indicators.get('002202.SZ', {}).get('price', {})

    signals = []

    close = safe_float(price_data.get('收盘价', {}).get('latest', 0))
    ma5 = safe_float(tech.get('5日MA简单移动平均', {}).get('latest', 0))
    ma20 = safe_float(tech.get('20日MA简单移动平均', {}).get('latest', 0))
    ma60 = safe_float(tech.get('60日MA简单移动平均', {}).get('latest', 0))
    diff = safe_float(tech.get('MACD指数平滑异同平均-DIFF', {}).get('latest', 0))
    dea = safe_float(tech.get('MACD指数平滑异同平均-DEA', {}).get('latest', 0))
    k = safe_float(tech.get('KDJ(K值)', {}).get('latest', 0))
    d = safe_float(tech.get('KDJ(D值)', {}).get('latest', 0))
    j = safe_float(tech.get('KDJ(J值)', {}).get('latest', 0))
    boll_low = safe_float(tech.get('BOLL布林线LOW', {}).get('latest', 0))

    if close == 0:
        return signals

    # Primary: Wait for KDJ pullback from overbought
    kdj_overbought = j > 90 or k > 80

    if kdj_overbought:
        signals.append(f"🔴 [金风科技] KDJ超买(J={j:.0f},K={k:.0f}) | 现价{close:.2f} 不要追高，等回调")
    elif j < 50 and k < 50:
        # KDJ returned to neutral/oversold
        if diff > dea:
            signals.append(f"🟢 [金风科技] KDJ回调到位(J={j:.0f})+MACD仍多头 | 现价{close:.2f} 第一批建仓")
        elif close <= ma20 * 1.03:
            signals.append(f"🟢 [金风科技] 回调至20日线({ma20:.2f})附近+KDJ修复 | 现价{close:.2f} 建仓")

    # Pullback to 20MA while MACD still positive
    pullback_near_20ma = close <= ma20 * 1.05
    if pullback_near_20ma and diff > dea and not kdj_overbought:
        signals.append(f"🟢 [金风科技] 回踩20日线({ma20:.2f})+MACD金叉 | 现价{close:.2f} 加仓")

    # Golden cross on pullback
    if diff > dea and close > ma5 and 30 < j < 70:
        signals.append(f"🟢 [金风科技] MACD金叉+站上5日线+KDJ修复 | 现价{close:.2f} 趋势确认买入")

    # Warning signals
    if diff < dea and close < ma60:
        signals.append(f"🔴 [金风科技] MACD死叉+跌破60日线 | 现价{close:.2f} 止损离场")

    return signals


def check_babi(indicators):
    """Check 巴比食品(605338.SH) buy conditions"""
    tech = indicators.get('605338.SH', {}).get('technical', {})
    price_data = indicators.get('605338.SH', {}).get('price', {})

    signals = []

    close = safe_float(price_data.get('收盘价', {}).get('latest', 0))
    volume = safe_float(price_data.get('成交量', {}).get('latest', 0))
    ma5 = safe_float(tech.get('5日MA简单移动平均', {}).get('latest', 0))
    ma20 = safe_float(tech.get('20日MA简单移动平均', {}).get('latest', 0))
    ma60 = safe_float(tech.get('60日MA简单移动平均', {}).get('latest', 0))
    diff = safe_float(tech.get('MACD指数平滑异同平均-DIFF', {}).get('latest', 0))
    dea = safe_float(tech.get('MACD指数平滑异同平均-DEA', {}).get('latest', 0))
    k = safe_float(tech.get('KDJ(K值)', {}).get('latest', 0))
    d = safe_float(tech.get('KDJ(D值)', {}).get('latest', 0))
    j = safe_float(tech.get('KDJ(J值)', {}).get('latest', 0))
    rsi = safe_float(tech.get('RSI相对强弱指标', {}).get('latest', 0))
    boll_low = safe_float(tech.get('BOLL布林线LOW', {}).get('latest', 0))
    boll_mid = safe_float(tech.get('BOLL布林线', {}).get('latest', 0))

    if close == 0:
        return signals

    # GRID bottom entry
    near_boll_low = close <= boll_low * 1.02
    if near_boll_low:
        signals.append(f"🟢 [巴比食品] 触及BOLL下轨({boll_low:.2f}) | 现价{close:.2f} 网格底仓买入")

    # Right-side confirmation: price > 5MA + volume expansion
    vol_ok = volume > 2500000  # ~2.5M shares
    above_5ma = close > ma5
    kdj_golden = k > d and k < 30  # KDJ golden cross in oversold zone

    if above_5ma and vol_ok:
        signals.append(f"🟢 [巴比食品] 站上5日线({ma5:.2f})+放量({volume/10000:.0f}万手) | 现价{close:.2f} 右侧确认买入")
    elif above_5ma:
        signals.append(f"🟡 [巴比食品] 站上5日线({ma5:.2f})但量能不足 | 现价{close:.2f} 关注放量确认")

    if kdj_golden and not above_5ma:
        signals.append(f"🟡 [巴比食品] KDJ超卖区金叉(K={k:.1f}>D={d:.1f})但未站上5日线 | 现价{close:.2f} 接近买入")

    # MACD turning positive
    macd_narrowing = diff < 0 and dea < 0 and diff > dea
    if macd_narrowing and close < ma20:
        signals.append(f"📊 [巴比食品] MACD底背离收窄中 | 现价{close:.2f} 筑底阶段，等待突破")

    # Full breakout: price > 20MA
    if close > ma20 and diff > dea:
        if j > 90 or k > 80:
            signals.append(f"⚠️ [巴比食品] 突破20日线但KDJ过热(J={j:.0f},K={k:.0f}) | 现价{close:.2f} 等回调加仓")
        else:
            signals.append(f"🟢 [巴比食品] 突破20日线({ma20:.2f})+MACD金叉 | 现价{close:.2f} 趋势逆转，重仓")

    # Stop loss warning
    if close < boll_low * 0.97:
        signals.append(f"🔴 [巴比食品] 跌破BOLL下轨({boll_low:.2f}) | 现价{close:.2f} 止损")

    # RSI recovery
    if rsi > 0 and rsi < 20:
        signals.append(f"📊 [巴比食品] RSI极度超卖({rsi:.1f}) | 现价{close:.2f} 反弹概率高,关注抄底")

    return signals


def check_tebian(indicators):
    """Check 特变电工(600089.SH) buy conditions — MA_CROSS策略，回测年化30% PF3.47"""
    tech = indicators.get('600089.SH', {}).get('technical', {})
    price_data = indicators.get('600089.SH', {}).get('price', {})

    signals = []

    close = safe_float(price_data.get('收盘价', {}).get('latest', 0))
    volume = safe_float(price_data.get('成交量', {}).get('latest', 0))
    ma5 = safe_float(tech.get('5日MA简单移动平均', {}).get('latest', 0))
    ma20 = safe_float(tech.get('20日MA简单移动平均', {}).get('latest', 0))
    ma60 = safe_float(tech.get('60日MA简单移动平均', {}).get('latest', 0))
    diff = safe_float(tech.get('MACD指数平滑异同平均-DIFF', {}).get('latest', 0))
    dea = safe_float(tech.get('MACD指数平滑异同平均-DEA', {}).get('latest', 0))
    k = safe_float(tech.get('KDJ(K值)', {}).get('latest', 0))
    d = safe_float(tech.get('KDJ(D值)', {}).get('latest', 0))
    j = safe_float(tech.get('KDJ(J值)', {}).get('latest', 0))
    rsi = safe_float(tech.get('RSI相对强弱指标', {}).get('latest', 0))

    if close == 0:
        return signals

    prefix = "[特变电工]"
    macd_bullish = diff > dea
    macd_golden_today = diff > dea and diff > 0  # DIFF翻红
    kdj_healthy = k < 80 and j < 100
    vol_ok = volume > 15000000  # ~15万手以上

    # ── 买入信号 ──

    # 第1批: MACD金叉确认 + KDJ未过热 → 现价建仓1/3
    if macd_golden_today and kdj_healthy and close > ma5:
        signals.append(f"🟢 {prefix} 第1批建仓 - MACD金叉(DIFF={diff:.2f}>0)+KDJ健康(K={k:.0f})+站上5日线 | 现价{close:.2f}")
    elif macd_bullish and kdj_healthy and close > ma5:
        signals.append(f"🟡 {prefix} MACD多头但DIFF未翻红(DIFF={diff:.2f}) | 现价{close:.2f} 观察等确认")

    # 第2批: 回踩MA20附近 + MACD未死叉 → 加仓
    if close <= ma20 * 1.03 and macd_bullish and kdj_healthy:
        signals.append(f"🟢 {prefix} 第2批加仓 - 回踩MA20({ma20:.2f})附近+MACD未死叉 | 现价{close:.2f}")
    elif close <= ma20 * 1.05 and macd_bullish:
        if k < 80:
            signals.append(f"🟡 {prefix} 接近MA20({ma20:.2f}) | 现价{close:.2f} 关注第2批加仓机会")

    # 第3批: 突破前高29.29 + 放量 → 趋势确认加仓
    if close > 29.29 and vol_ok and macd_bullish and kdj_healthy:
        signals.append(f"🟢 {prefix} 第3批加仓 - 突破前高29.29+放量+MACD多头 | 现价{close:.2f} 趋势确认")

    # ── 警告信号 ──

    # KDJ过热预警
    if j > 100 or k > 80:
        signals.append(f"⚠️ {prefix} KDJ过热(J={j:.0f},K={k:.0f}) | 现价{close:.2f} 暂停加仓等修复")
    elif j > 90:
        signals.append(f"📊 {prefix} KDJ接近过热(J={j:.0f}) | 现价{close:.2f} 谨慎追高")

    # RSI超买
    if rsi > 70:
        signals.append(f"📊 {prefix} RSI超买({rsi:.1f}) | 现价{close:.2f} 短线不追高")

    # 接近目标位
    if close > 31.00:
        signals.append(f"📊 {prefix} 接近第一止盈位32.00 | 现价{close:.2f} 准备分批止盈")
    elif close > 32.00:
        signals.append(f"🏁 {prefix} 到达第一止盈目标32.00 | 现价{close:.2f} 减仓1/3")

    # ── 止损信号 ──

    if close < ma20 and not macd_bullish:
        signals.append(f"🔴 {prefix} 跌破MA20({ma20:.2f})+MACD死叉 | 现价{close:.2f} 减半仓")
    elif close < ma20:
        signals.append(f"⚠️ {prefix} 跌破MA20({ma20:.2f}) | 现价{close:.2f} 关注MACD是否跟随死叉")

    if close < ma60:
        signals.append(f"🔴 {prefix} 跌破MA60({ma60:.2f}) | 现价{close:.2f} 清仓离场")

    return signals


def check_ma_trend(code, name, indicators):
    """Generic MA trend-following strategy (MA_CROSS best in backtest)"""
    tech = indicators.get(code, {}).get('technical', {})
    price_data = indicators.get(code, {}).get('price', {})

    signals = []
    close = safe_float(price_data.get('收盘价', {}).get('latest', 0))
    ma5 = safe_float(tech.get('5日MA简单移动平均', {}).get('latest', 0))
    ma20 = safe_float(tech.get('20日MA简单移动平均', {}).get('latest', 0))
    ma60 = safe_float(tech.get('60日MA简单移动平均', {}).get('latest', 0))
    diff = safe_float(tech.get('MACD指数平滑异同平均-DIFF', {}).get('latest', 0))
    dea = safe_float(tech.get('MACD指数平滑异同平均-DEA', {}).get('latest', 0))
    k = safe_float(tech.get('KDJ(K值)', {}).get('latest', 0))
    d = safe_float(tech.get('KDJ(D值)', {}).get('latest', 0))
    j = safe_float(tech.get('KDJ(J值)', {}).get('latest', 0))
    boll_up = safe_float(tech.get('BOLL布林线UP', {}).get('latest', 0))

    if close == 0:
        return signals

    prefix = f"[{name}]"
    macd_bullish = diff > dea
    ma_aligned = ma5 > ma20 > ma60 and close > ma5
    kdj_neutral = 20 < k < 80 and 20 < d < 80

    # Primary: trend following entry
    if macd_bullish and ma_aligned and kdj_neutral:
        signals.append(f"🟢 {prefix} 主升浪买入 - MACD多头+均线多头+KDJ中性 | 现价{close:.2f}")
    elif macd_bullish and ma_aligned:
        if j > 100:
            signals.append(f"⚠️ {prefix} 趋势多头但KDJ超买(J={j:.0f})，等回调不追高 | 现价{close:.2f}")
        elif k > 80:
            signals.append(f"🟡 {prefix} 趋势多头但KDJ高位(K={k:.0f})，轻仓 | 现价{close:.2f}")

    # Secondary: pullback to 20MA
    if close <= ma20 * 1.02 and macd_bullish:
        if j > 90 or k > 80:
            signals.append(f"⚠️ {prefix} 回调至20日均线但KDJ过热(J={j:.0f},K={k:.0f}) | 现价{close:.2f} 等KDJ修复后加仓")
        else:
            signals.append(f"🟢 {prefix} 回调至20日均线({ma20:.2f})附近+MACD未死叉 | 现价{close:.2f} 加仓")

    # KDJ oversold bounce
    if j < 25 and k < 30:
        if diff > dea:
            signals.append(f"🟢 {prefix} KDJ超卖区(J={j:.0f})+MACD仍多头 | 现价{close:.2f} 超卖反弹买入")
        else:
            signals.append(f"📊 {prefix} KDJ超卖(J={j:.0f}) | 现价{close:.2f} 关注MACD金叉确认")

    # Warnings
    if diff < dea and close < ma20:
        signals.append(f"🔴 {prefix} MACD死叉+跌破20日线 | 现价{close:.2f} 建议减仓/清仓")
    if boll_up > 0 and close > boll_up * 0.98:
        signals.append(f"📊 {prefix} 接近BOLL上轨({boll_up:.2f}) | 现价{close:.2f} 注意压力")
    if close < ma60:
        signals.append(f"🔴 {prefix} 跌破60日均线({ma60:.2f}) | 现价{close:.2f} 清仓离场")

    return signals


def check_kdj_generic(code, name, indicators):
    """Generic KDJ strategy (KDJ best in backtest for these stocks)"""
    tech = indicators.get(code, {}).get('technical', {})
    price_data = indicators.get(code, {}).get('price', {})

    signals = []
    close = safe_float(price_data.get('收盘价', {}).get('latest', 0))
    ma5 = safe_float(tech.get('5日MA简单移动平均', {}).get('latest', 0))
    ma20 = safe_float(tech.get('20日MA简单移动平均', {}).get('latest', 0))
    ma60 = safe_float(tech.get('60日MA简单移动平均', {}).get('latest', 0))
    diff = safe_float(tech.get('MACD指数平滑异同平均-DIFF', {}).get('latest', 0))
    dea = safe_float(tech.get('MACD指数平滑异同平均-DEA', {}).get('latest', 0))
    k = safe_float(tech.get('KDJ(K值)', {}).get('latest', 0))
    d = safe_float(tech.get('KDJ(D值)', {}).get('latest', 0))
    j = safe_float(tech.get('KDJ(J值)', {}).get('latest', 0))

    if close == 0:
        return signals

    prefix = f"[{name}]"
    kdj_golden = k > d
    kdj_overbought = j > 90 or k > 80
    kdj_oversold = j < 25

    # Primary: KDJ golden cross in neutral zone
    if kdj_golden and 30 < k < 70 and close > ma5:
        signals.append(f"🟢 {prefix} KDJ金叉(K={k:.0f}>D={d:.0f})+站上5日线 | 现价{close:.2f} KDJ策略买入")
    elif kdj_golden and k < 30:
        signals.append(f"🟢 {prefix} KDJ超卖区金叉(K={k:.0f}>D={d:.0f}) | 现价{close:.2f} 底部金叉买入")

    # Secondary: KDJ turning from oversold
    if kdj_oversold and diff > dea:
        signals.append(f"🟢 {prefix} KDJ超卖(J={j:.0f})+MACD多头 | 现价{close:.2f} 分批建仓")
    elif kdj_oversold:
        signals.append(f"📊 {prefix} KDJ超卖区(J={j:.0f}) | 现价{close:.2f} 等待MACD确认")

    # Reversal: KDJ death cross to golden cross recovery
    if kdj_golden and close > ma20 and diff > dea:
        if kdj_overbought:
            signals.append(f"⚠️ {prefix} KDJ金叉但超买(J={j:.0f},K={k:.0f}) | 现价{close:.2f} 等回调不追高")
        else:
            signals.append(f"🟢 {prefix} KDJ金叉+站上20日线+MACD多头 | 现价{close:.2f} 趋势确认加仓")

    # Warnings
    if kdj_overbought:
        signals.append(f"🔴 {prefix} KDJ超买(J={j:.0f},K={k:.0f}) | 现价{close:.2f} 不要追高，等回调")
    if diff < dea and close < ma20:
        signals.append(f"🔴 {prefix} MACD死叉+跌破20日线 | 现价{close:.2f} 止损/减仓")
    if close < ma60:
        signals.append(f"🔴 {prefix} 跌破60日均线({ma60:.2f}) | 现价{close:.2f} 清仓离场")

    return signals


def check_grid_generic(code, name, indicators):
    """Generic grid/bollinger strategy for range-bound stocks"""
    tech = indicators.get(code, {}).get('technical', {})
    price_data = indicators.get(code, {}).get('price', {})

    signals = []
    close = safe_float(price_data.get('收盘价', {}).get('latest', 0))
    ma5 = safe_float(tech.get('5日MA简单移动平均', {}).get('latest', 0))
    ma20 = safe_float(tech.get('20日MA简单移动平均', {}).get('latest', 0))
    ma60 = safe_float(tech.get('60日MA简单移动平均', {}).get('latest', 0))
    diff = safe_float(tech.get('MACD指数平滑异同平均-DIFF', {}).get('latest', 0))
    dea = safe_float(tech.get('MACD指数平滑异同平均-DEA', {}).get('latest', 0))
    k = safe_float(tech.get('KDJ(K值)', {}).get('latest', 0))
    j = safe_float(tech.get('KDJ(J值)', {}).get('latest', 0))
    boll_mid = safe_float(tech.get('BOLL布林线', {}).get('latest', 0))
    boll_low = safe_float(tech.get('BOLL布林线LOW', {}).get('latest', 0))
    boll_up = safe_float(tech.get('BOLL布林线UP', {}).get('latest', 0))

    if close == 0:
        return signals

    prefix = f"[{name}]"

    # Grid entry: near BOLL lower band
    if boll_low > 0 and close <= boll_low * 1.03:
        signals.append(f"🟢 {prefix} 触及BOLL下轨({boll_low:.2f}) | 现价{close:.2f} 网格底仓买入")
    elif boll_mid > 0 and close <= boll_mid * 1.02:
        signals.append(f"🟡 {prefix} 回调至BOLL中轨({boll_mid:.2f}) | 现价{close:.2f} 网格加仓")

    # KDJ oversold
    if j < 25:
        signals.append(f"🟢 {prefix} KDJ超卖(J={j:.0f}) | 现价{close:.2f} 网格买入信号")

    # Price below 20MA with KDJ low - grid accumulation zone
    if close < ma20 and j < 40 and diff > dea:
        signals.append(f"🟢 {prefix} 20日线下+KDJ低位(J={j:.0f})+MACD多头 | 现价{close:.2f} 网格分批建仓")

    # Warnings
    if boll_up > 0 and close > boll_up * 0.98:
        signals.append(f"📊 {prefix} 接近BOLL上轨({boll_up:.2f}) | 现价{close:.2f} 网格止盈区")
    if j > 90:
        signals.append(f"🔴 {prefix} KDJ超买(J={j:.0f},K={k:.0f}) | 现价{close:.2f} 暂停网格买入")
    if diff < dea and close < ma60:
        signals.append(f"🔴 {prefix} MACD死叉+跌破60日线 | 现价{close:.2f} 暂停网格，等企稳")

    return signals


def check_boll_grid(code, name, indicators):
    """Bollinger band strategy (best for 深南电路)"""
    tech = indicators.get(code, {}).get('technical', {})
    price_data = indicators.get(code, {}).get('price', {})

    signals = []
    close = safe_float(price_data.get('收盘价', {}).get('latest', 0))
    ma5 = safe_float(tech.get('5日MA简单移动平均', {}).get('latest', 0))
    ma20 = safe_float(tech.get('20日MA简单移动平均', {}).get('latest', 0))
    ma60 = safe_float(tech.get('60日MA简单移动平均', {}).get('latest', 0))
    diff = safe_float(tech.get('MACD指数平滑异同平均-DIFF', {}).get('latest', 0))
    dea = safe_float(tech.get('MACD指数平滑异同平均-DEA', {}).get('latest', 0))
    k = safe_float(tech.get('KDJ(K值)', {}).get('latest', 0))
    j = safe_float(tech.get('KDJ(J值)', {}).get('latest', 0))
    rsi = safe_float(tech.get('RSI相对强弱指标', {}).get('latest', 0))
    boll_mid = safe_float(tech.get('BOLL布林线', {}).get('latest', 0))
    boll_low = safe_float(tech.get('BOLL布林线LOW', {}).get('latest', 0))
    boll_up = safe_float(tech.get('BOLL布林线UP', {}).get('latest', 0))

    if close == 0:
        return signals

    prefix = f"[{name}]"

    # Primary: BOLL lower band reversal
    if boll_low > 0 and close <= boll_low * 1.02:
        signals.append(f"🟢 {prefix} 触及BOLL下轨({boll_low:.2f}) | 现价{close:.2f} 布林下轨买入")
    elif boll_mid > 0 and close <= boll_mid * 1.01 and diff > dea:
        signals.append(f"🟢 {prefix} 回调至BOLL中轨({boll_mid:.2f})+MACD多头 | 现价{close:.2f} 中轨支撑买入")

    # Strong trend: price riding BOLL upper with MACD confirmation
    if boll_up > 0 and close > boll_up * 0.95 and diff > dea and 50 < k < 85:
        signals.append(f"🟢 {prefix} 布林开口向上+MACD多头+KDJ健康 | 现价{close:.2f} 趋势延续买入")

    # Pullback entry
    if close < ma5 and close > ma20 and j < 40 and diff > dea:
        signals.append(f"🟢 {prefix} 缩量回踩5日线+KDJ修复 | 现价{close:.2f} 回调买入")

    # RSI oversold
    if rsi > 0 and rsi < 30:
        signals.append(f"📊 {prefix} RSI超卖({rsi:.1f}) | 现价{close:.2f} 关注反弹")

    # Warnings
    if boll_up > 0 and close > boll_up * 1.02:
        signals.append(f"🔴 {prefix} 突破BOLL上轨({boll_up:.2f}) | 现价{close:.2f} 超买减仓")
    if diff < dea and close < ma20:
        signals.append(f"🔴 {prefix} MACD死叉+跌破20日线 | 现价{close:.2f} 止损")
    if close < ma60:
        signals.append(f"🔴 {prefix} 跌破60日均线({ma60:.2f}) | 现价{close:.2f} 清仓")

    return signals


def push_bark(title: str, body: str, group: str = "monitor") -> bool:
    """Push notification via Bark to mobile phone."""
    if not BARK_KEY:
        print("[Bark] BARK_KEY not set, skip push", file=sys.stderr)
        return False

    payload = {
        "device_key": BARK_KEY,
        "title": title,
        "body": body,
        "badge": 1,
        "sound": "default",
        "group": group,
    }
    try:
        req = urllib.request.Request(
            BARK_URL,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                print(f"[Bark] 推送成功: {title}")
                return True
            else:
                print(f"[Bark] 推送失败: {resp.status}", file=sys.stderr)
                return False
    except Exception as e:
        print(f"[Bark] 推送异常: {e}", file=sys.stderr)
        return False


def write_state_file(all_indicators, buy_signals, now):
    """Write current stock prices and active signals to a JSON state file."""
    stocks = {}
    for cfg in STOCK_CONFIGS:
        name, code = cfg[1], cfg[2]
        data = all_indicators.get(code, {})
        tech = data.get('technical', {})
        price_data = data.get('price', {})
        stocks[code] = {
            'name': name,
            'strategy': cfg[3],
            'close': safe_float(price_data.get('收盘价', {}).get('latest', 0)),
            'ma5': safe_float(tech.get('5日MA简单移动平均', {}).get('latest', 0)),
            'ma20': safe_float(tech.get('20日MA简单移动平均', {}).get('latest', 0)),
            'ma60': safe_float(tech.get('60日MA简单移动平均', {}).get('latest', 0)),
            'macd_diff': safe_float(tech.get('MACD指数平滑异同平均-DIFF', {}).get('latest', 0)),
            'macd_dea': safe_float(tech.get('MACD指数平滑异同平均-DEA', {}).get('latest', 0)),
            'kdj_k': safe_float(tech.get('KDJ(K值)', {}).get('latest', 0)),
            'kdj_d': safe_float(tech.get('KDJ(D值)', {}).get('latest', 0)),
            'kdj_j': safe_float(tech.get('KDJ(J值)', {}).get('latest', 0)),
        }
    state = {
        'updated': now.strftime('%Y-%m-%d %H:%M:%S'),
        'stocks': stocks,
        'active_signals': buy_signals,
    }
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def main():
    if not MX_APIKEY:
        print("[ERROR] MX_APIKEY not set", file=sys.stderr)
        sys.exit(1)

    now = datetime.now()
    print(f"\n{'='*60}")
    print(f"买入信号监控 - {now.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}")

    # Load backtest gate cache (avoids DB hits on every run)
    _load_backtest_cache()

    # Fetch data for all stocks in parallel (3x faster than sequential batches)
    all_indicators = {}
    query_names = [s[0] for s in STOCK_CONFIGS]
    batch_size = 4
    batches = [query_names[i:i + batch_size] for i in range(0, len(query_names), batch_size)]

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
        print("[ERROR] Failed to fetch data")
        sys.exit(1)

    # Dispatch to checker, then apply backtest gate per stock
    def _make_checker(cfg):
        code, strategy, ctype = cfg[2], cfg[3], cfg[4]
        if ctype == "custom_lixun":
            return check_lixun(all_indicators)
        elif ctype == "custom_jinfeng":
            return check_jinfeng(all_indicators)
        elif ctype == "custom_babi":
            return check_babi(all_indicators)
        elif ctype == "custom_tebian":
            return check_tebian(all_indicators)
        elif ctype == "generic_ma_trend":
            return check_ma_trend(code, cfg[1], all_indicators)
        elif ctype == "generic_kdj":
            return check_kdj_generic(code, cfg[1], all_indicators)
        elif ctype == "generic_grid":
            return check_grid_generic(code, cfg[1], all_indicators)
        elif ctype == "generic_boll_grid":
            return check_boll_grid(code, cfg[1], all_indicators)
        return []

    all_signals = []
    for cfg in STOCK_CONFIGS:
        raw = _make_checker(cfg)
        # Apply backtest gate: downgrade 🟢 signals if strategy not historically profitable
        filtered = apply_backtest_gate(raw, cfg[2], cfg[3])
        all_signals.extend(filtered)

    # Persist backtest cache for next run
    _save_backtest_cache()

    # Collect buy signals
    buy_signals = [s for s in all_signals if s.startswith('🟢')]

    # Read previous signals BEFORE overwriting state file (for dedup)
    def _signal_key(s: str) -> str:
        """Extract signal identity ignoring price: '🟢 [name] signal_type | ...'"""
        import re
        return re.sub(r' \| 现价[\d.]+.*$', '', s)

    prev_keys = set()
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                prev = json.load(f)
            prev_keys = {_signal_key(s) for s in prev.get("active_signals", [])}
        except (json.JSONDecodeError, KeyError):
            pass

    # Write state file with current prices (now prev_keys is safely captured)
    write_state_file(all_indicators, buy_signals, now)

    if all_signals:
        print("\n📢 信号汇总:")
        for s in all_signals:
            print(f"  {s}")

        # Highlight actionable signals
        if buy_signals:
            alert_msg = f"\n🚨 共 {len(buy_signals)} 个买入信号!"
            print(alert_msg)
            for s in buy_signals:
                print(f"  >>> {s}")
            # Persist alerts to log
            with open(ALERT_LOG, 'a') as f:
                f.write(f"{'='*60}\n")
                f.write(f"买入信号 - {now.strftime('%Y-%m-%d %H:%M:%S')}\n")
                for s in buy_signals:
                    f.write(f"  {s}\n")
                f.write(f"{'='*60}\n\n")
            # Push to mobile via Bark (dedup: only push newly appeared signals)
            current_keys = {_signal_key(s) for s in buy_signals}
            new_keys = current_keys - prev_keys
            if new_keys:
                new_signals = [s for s in buy_signals if _signal_key(s) in new_keys]
                body = "\n".join(new_signals)
                push_bark(f"🔥 新买入信号 {now.strftime('%H:%M')}", body)
            elif not prev_keys and buy_signals:
                # First run, push all current signals
                body = "\n".join(buy_signals)
                push_bark(f"🔥 买入信号 {now.strftime('%H:%M')}", body)
    else:
        print("\n✅ 所有股票无异常信号，继续等待。")

    # Quick summary
    print(f"\n{'─'*60}")
    for cfg in STOCK_CONFIGS:
        name, code = cfg[1], cfg[2]
        data = all_indicators.get(code, {})
        tech = data.get('technical', {})
        price_data = data.get('price', {})
        close = price_data.get('收盘价', {}).get('latest', 'N/A')
        k_val = tech.get('KDJ(K值)', {}).get('latest', 'N/A')
        j_val = tech.get('KDJ(J值)', {}).get('latest', 'N/A')
        macd_diff = tech.get('MACD指数平滑异同平均-DIFF', {}).get('latest', 'N/A')
        macd_dea = tech.get('MACD指数平滑异同平均-DEA', {}).get('latest', 'N/A')
        strategy = cfg[3]
        print(f"  {name}({code}) 策略:{strategy} | 收盘{close} | KDJ-K:{k_val} J:{j_val} | MACD DIFF:{macd_diff} DEA:{macd_dea}")
    print(f"{'─'*60}\n")


if __name__ == '__main__':
    main()
