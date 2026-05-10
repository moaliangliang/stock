#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
三支股票买入信号监控脚本
检查: 立讯精密(002475), 金风科技(002202), 巴比食品(605338)
"""

import os
import sys
import json
import urllib.request
from datetime import datetime, time

# Config
ALERT_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'buy_signal_alerts.log')

# mx-data API
MX_APIKEY = os.environ.get("MX_APIKEY", "")
MX_URL = "https://mkapi2.dfcfs.com/finskillshub/api/claw/query"

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


def parse_indicators(raw_data):
    """Parse raw API response into structured indicator dict"""
    tables = raw_data['data']['data']['searchDataResultDTO']['dataTableDTOList']
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
    close = float(price_data.get('收盘价', {}).get('latest', 0))
    ma5 = float(tech.get('5日MA简单移动平均', {}).get('latest', 0))
    ma20 = float(tech.get('20日MA简单移动平均', {}).get('latest', 0))
    ma60 = float(tech.get('60日MA简单移动平均', {}).get('latest', 0))
    diff = float(tech.get('MACD指数平滑异同平均-DIFF', {}).get('latest', 0))
    dea = float(tech.get('MACD指数平滑异同平均-DEA', {}).get('latest', 0))
    k = float(tech.get('KDJ(K值)', {}).get('latest', 0))
    d = float(tech.get('KDJ(D值)', {}).get('latest', 0))
    j = float(tech.get('KDJ(J值)', {}).get('latest', 0))
    boll_mid = float(tech.get('BOLL布林线', {}).get('latest', 0))
    boll_up = float(tech.get('BOLL布林线UP', {}).get('latest', 0))

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

    close = float(price_data.get('收盘价', {}).get('latest', 0))
    ma5 = float(tech.get('5日MA简单移动平均', {}).get('latest', 0))
    ma20 = float(tech.get('20日MA简单移动平均', {}).get('latest', 0))
    ma60 = float(tech.get('60日MA简单移动平均', {}).get('latest', 0))
    diff = float(tech.get('MACD指数平滑异同平均-DIFF', {}).get('latest', 0))
    dea = float(tech.get('MACD指数平滑异同平均-DEA', {}).get('latest', 0))
    k = float(tech.get('KDJ(K值)', {}).get('latest', 0))
    d = float(tech.get('KDJ(D值)', {}).get('latest', 0))
    j = float(tech.get('KDJ(J值)', {}).get('latest', 0))
    boll_low = float(tech.get('BOLL布林线LOW', {}).get('latest', 0))

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

    close = float(price_data.get('收盘价', {}).get('latest', 0))
    volume = float(price_data.get('成交量', {}).get('latest', 0))
    ma5 = float(tech.get('5日MA简单移动平均', {}).get('latest', 0))
    ma20 = float(tech.get('20日MA简单移动平均', {}).get('latest', 0))
    ma60 = float(tech.get('60日MA简单移动平均', {}).get('latest', 0))
    diff = float(tech.get('MACD指数平滑异同平均-DIFF', {}).get('latest', 0))
    dea = float(tech.get('MACD指数平滑异同平均-DEA', {}).get('latest', 0))
    k = float(tech.get('KDJ(K值)', {}).get('latest', 0))
    d = float(tech.get('KDJ(D值)', {}).get('latest', 0))
    j = float(tech.get('KDJ(J值)', {}).get('latest', 0))
    rsi = float(tech.get('RSI相对强弱指标', {}).get('latest', 0))
    boll_low = float(tech.get('BOLL布林线LOW', {}).get('latest', 0))
    boll_mid = float(tech.get('BOLL布林线', {}).get('latest', 0))

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
        signals.append(f"🟢 [巴比食品] 突破20日线({ma20:.2f})+MACD金叉 | 现价{close:.2f} 趋势逆转，重仓")

    # Stop loss warning
    if close < boll_low * 0.97:
        signals.append(f"🔴 [巴比食品] 跌破BOLL下轨({boll_low:.2f}) | 现价{close:.2f} 止损")

    # RSI recovery
    if rsi > 0 and rsi < 20:
        signals.append(f"📊 [巴比食品] RSI极度超卖({rsi:.1f}) | 现价{close:.2f} 反弹概率高,关注抄底")

    return signals


def main():
    if not MX_APIKEY:
        print("[ERROR] MX_APIKEY not set", file=sys.stderr)
        sys.exit(1)

    now = datetime.now()
    print(f"\n{'='*60}")
    print(f"买入信号监控 - {now.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}")

    # Fetch data
    stocks = ["立讯精密 002475", "金风科技 002202", "巴比食品 605338"]
    raw = fetch_technical_data(stocks)
    if not raw:
        print("[ERROR] Failed to fetch data")
        sys.exit(1)

    indicators = parse_indicators(raw)

    # Check each stock
    all_signals = []
    all_signals.extend(check_lixun(indicators))
    all_signals.extend(check_jinfeng(indicators))
    all_signals.extend(check_babi(indicators))

    if all_signals:
        print("\n📢 信号汇总:")
        for s in all_signals:
            print(f"  {s}")

        # Highlight actionable signals
        buy_signals = [s for s in all_signals if s.startswith('🟢')]
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
    else:
        print("\n✅ 所有股票无异常信号，继续等待。")

    # Quick summary
    print(f"\n{'─'*60}")
    for symbol, data in indicators.items():
        tech = data.get('technical', {})
        price_data = data.get('price', {})
        close = price_data.get('收盘价', {}).get('latest', 'N/A')
        k_val = tech.get('KDJ(K值)', {}).get('latest', 'N/A')
        j_val = tech.get('KDJ(J值)', {}).get('latest', 'N/A')
        macd_diff = tech.get('MACD指数平滑异同平均-DIFF', {}).get('latest', 'N/A')
        macd_dea = tech.get('MACD指数平滑异同平均-DEA', {}).get('latest', 'N/A')
        print(f"  {symbol}: 收盘{close} | KDJ-K:{k_val} J:{j_val} | MACD DIFF:{macd_diff} DEA:{macd_dea}")
    print(f"{'─'*60}\n")


if __name__ == '__main__':
    main()
