#!/usr/bin/env python3
"""Batch analyze all stocks in a fund portfolio."""
import sys, os
sys.path.insert(0, '.')
from datetime import datetime, timezone
from app.core.database import SyncSessionLocal
from app.models.market_data import KLine
from app.services.backtest import run_backtest
from app.models.strategy import StrategyType
from sqlalchemy import select
import numpy as np

FUND = '中欧数字经济混合C (018994)'
FUND_HOLDINGS = [
    ('300308.SZ', '中际旭创', 14.49),
    ('300502.SZ', '新易盛', 14.30),
    ('002916.SZ', '深南电路', 8.14),
    ('603179.SH', '新泉股份', 8.01),
    ('600183.SH', '生益科技', 7.49),
    ('002463.SZ', '沪电股份', 7.29),
    ('300274.SZ', '阳光电源', 6.55),
    ('000977.SZ', '浪潮信息', 6.16),
    ('002384.SZ', '东山精密', 5.95),
]

STRATEGIES = [
    ('MA交叉', StrategyType.MA_CROSS, {'fast_ma': 5, 'slow_ma': 20}),
    ('MACD', StrategyType.MACD, {'fast': 12, 'slow': 26, 'signal': 9}),
    ('KDJ', StrategyType.KDJ, {'n': 9, 'k': 3, 'd': 3}),
    ('布林带', StrategyType.BOLLINGER, {'period': 20, 'std': 2.0}),
    ('网格', StrategyType.GRID, {'grid_count': 5, 'grid_spread': 0.03}),
]

def ema(arr, p):
    out = np.zeros_like(arr)
    out[0] = arr[0]
    m = 2 / (p + 1)
    for i in range(1, len(arr)):
        out[i] = (arr[i] - out[i-1]) * m + out[i-1]
    return out

def wilder_ema(arr, period):
    out = np.full_like(arr, np.nan, dtype=float)
    for i in range(len(arr)):
        if not np.isnan(arr[i]):
            out[i] = float(arr[i])
            break
    alpha = 1.0 / period
    for i in range(1, len(arr)):
        if not np.isnan(arr[i]) and not np.isnan(out[i - 1]):
            out[i] = alpha * float(arr[i]) + (1 - alpha) * out[i - 1]
        elif not np.isnan(arr[i]):
            out[i] = float(arr[i])
    return out

def compute_indicators(klines):
    closes = np.array([k["close"] for k in klines])
    highs = np.array([k["high"] for k in klines])
    lows = np.array([k["low"] for k in klines])
    volumes = np.array([k["volume"] for k in klines])
    n = len(closes)
    current = closes[-1]

    # RSI (14) — Wilder's smoothing
    delta_all = np.diff(closes, prepend=closes[0])
    gain_arr = np.where(delta_all > 0, delta_all, 0)
    loss_arr = np.where(delta_all < 0, -delta_all, 0)
    avg_gain = wilder_ema(gain_arr, 14)[-1]
    avg_loss = wilder_ema(loss_arr, 14)[-1]
    if not np.isnan(avg_gain) and not np.isnan(avg_loss) and avg_loss > 0:
        rsi = 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))
    else:
        rsi = 100.0 if avg_loss == 0 else 50.0
    ma5 = np.mean(closes[-5:])
    ma20 = np.mean(closes[-20:])
    ma60 = np.mean(closes[-60:]) if n >= 60 else ma20
    ma_trend = "多头" if ma5 > ma20 > ma60 else "空头" if ma5 < ma20 < ma60 else "震荡"
    change_5d = (closes[-1] / closes[-6] - 1) * 100 if n >= 6 else 0
    vol_ratio = np.mean(volumes[-5:]) / np.mean(volumes[-20:]) if n >= 20 else 1
    low_n = np.min(lows[-9:])
    high_n = np.max(highs[-9:])
    rsv = (current - low_n) / (high_n - low_n) * 100 if high_n > low_n else 50
    k_val = rsv * 1/3 + 50 * 2/3
    d_val = k_val * 1/3 + 50 * 2/3
    j_val = 3 * k_val - 2 * d_val
    cmf = 0
    if n >= 20:
        mf = ((closes[-20:] - lows[-20:]) - (highs[-20:] - closes[-20:])) / (highs[-20:] - lows[-20:] + 1e-9)
        cmf = np.sum(mf * volumes[-20:]) / np.sum(volumes[-20:])
    ema12 = ema(closes, 12)
    ema26 = ema(closes, 26)
    macd_line = ema12 - ema26
    signal = ema(macd_line, 9)
    macd_hist = macd_line[-1] - signal[-1]
    macd_div = False
    if n >= 40:
        hist_min = np.min(macd_line[-20:] - signal[-20:])
        if closes[-1] < closes[-20] and macd_hist > hist_min:
            macd_div = True
    golden = False
    death = False
    if n >= 21:
        ma5_prev = np.mean(closes[-7:-2])
        ma20_prev = np.mean(closes[-22:-2])
        golden = ma5_prev <= ma20_prev and ma5 > ma20
        death = ma5_prev >= ma20_prev and ma5 < ma20
    return {
        "price": round(current, 2), "rsi": round(rsi, 1),
        "ma5": round(ma5, 2), "ma20": round(ma20, 2),
        "ma_trend": ma_trend, "change_5d": round(change_5d, 1),
        "vol_ratio": round(vol_ratio, 2), "kdj_j": round(j_val, 1),
        "kdj_k": round(k_val, 1), "cmf": round(cmf, 4),
        "macd_hist": round(macd_hist, 4), "macd_div": macd_div,
        "golden_cross": golden, "death_cross": death,
    }

def classify_style(bt):
    trend = max(bt["MA交叉"]["annual_return"], bt["MACD"]["annual_return"])
    reversal = max(bt["KDJ"]["annual_return"], bt["布林带"]["annual_return"])
    if trend > reversal + 5:
        return ("趋势型", "顺势追涨")
    elif reversal > trend + 5:
        return ("震荡型", "抄底逃顶")
    else:
        return ("混合型", "趋势+反转")

# Run all analyses
results = []
db = SyncSessionLocal()
for sym, name, weight in FUND_HOLDINGS:
    rows = db.execute(
        select(KLine)
        .where(KLine.symbol == sym, KLine.interval == '1d')
        .order_by(KLine.timestamp.asc())
    ).scalars().all()

    klines = [{'timestamp': int(r.timestamp.timestamp()), 'open': r.open, 'high': r.high,
               'low': r.low, 'close': r.close, 'volume': r.volume} for r in rows]

    ind = compute_indicators(klines)
    bt = {}
    for sname, stype, params in STRATEGIES:
        r = run_backtest(stype, params, klines)
        bt[sname] = {
            "total_return": round(r["total_return"] * 100, 1),
            "annual_return": round(r["annual_return"] * 100, 1),
            "max_drawdown": round(r["max_drawdown"] * 100, 1),
            "sharpe": round(r["sharpe_ratio"], 2),
            "win_rate": round(r["win_rate"] * 100, 1),
            "trades": r["total_trades"],
        }

    style, style_desc = classify_style(bt)
    best_st = max(bt.items(), key=lambda x: x[1]["annual_return"])

    score = 50
    if ind["ma_trend"] == "多头": score += 10
    elif ind["ma_trend"] == "空头": score -= 8
    if ind["rsi"] < 30: score += 8
    elif ind["rsi"] > 70: score -= 5
    if ind["macd_div"]: score += 5
    if ind["golden_cross"]: score += 8
    if ind["death_cross"]: score -= 8
    if ind["cmf"] > 0.1: score += 5
    elif ind["cmf"] < -0.1: score -= 5

    results.append({
        "symbol": sym, "name": name, "weight": weight,
        "ind": ind, "bt": bt, "style": style, "style_desc": style_desc,
        "best_st": best_st[0], "best_annual": best_st[1]["annual_return"],
        "score": score,
    })

db.close()

# Sort by weight
results.sort(key=lambda x: x["weight"], reverse=True)

# Print report
SEP80 = "-" * 80
SEP100 = "-" * 100

print("")
print(SEP100)
print(f"  {FUND} -- 前十大成分股深度分析")
print(f"  报告日期: {datetime.now().strftime('%Y-%m-%d')}")
print(SEP100)

# Summary
print("")
print(f'  {"代码":<12} {"名称":<10} {"仓位%":>6} {"现价":>8} {"RSI":>5} {"J":>6} {"趋势":<4} {"5日%":>7} {"CMF":>7} {"评分":>4} {"风格":<6} {"最佳策略":<8} {"年化%":>7}')
print("  " + "-" * 98)

for r in results:
    ind = r["ind"]
    signals = ""
    if ind["golden_cross"]: signals += " G"
    if ind["death_cross"]: signals += " D"
    if ind["macd_div"]: signals += " DIV"
    print(f'  {r["symbol"]:<12} {r["name"]:<10} {r["weight"]:>5.1f}% {ind["price"]:>8.2f} {ind["rsi"]:>5.1f} {ind["kdj_j"]:>6.1f} {ind["ma_trend"]:<4} {ind["change_5d"]:>+6.1f}% {ind["cmf"]:>7.3f} {r["score"]:>4} {r["style"]:<6} {r["best_st"]:<8} {r["best_annual"]:>+6.1f}%{signals}')

# Backtest comparison
print("")
print("")
print("  [五策略回测年化收益对比]")
print(f'  {"代码":<12} {"名称":<10} {"风格":<6} {"MA交叉":>8} {"MACD":>8} {"KDJ":>8} {"布林带":>8} {"网格":>8}   {"最佳":<8}')
print("  " + "-" * 100)

for r in results:
    bt = r["bt"]
    best = max(bt.items(), key=lambda x: x[1]["annual_return"])
    print(f'  {r["symbol"]:<12} {r["name"]:<10} {r["style"]:<6} '
          f'{bt["MA交叉"]["annual_return"]:>+7.1f}% {bt["MACD"]["annual_return"]:>+7.1f}% '
          f'{bt["KDJ"]["annual_return"]:>+7.1f}% {bt["布林带"]["annual_return"]:>+7.1f}% '
          f'{bt["网格"]["annual_return"]:>+7.1f}%   {best[0]}')

# Per-stock detail
for r in results:
    ind = r["ind"]
    bt = r["bt"]
    print("")
    print("  " + SEP80)
    print(f'  {r["symbol"]} {r["name"]} | 仓位 {r["weight"]}% | {r["style"]}({r["style_desc"]}) | 评分: {r["score"]}')
    print("  " + SEP80)
    print(f'  现价={ind["price"]} | RSI={ind["rsi"]} | K={ind["kdj_k"]} J={ind["kdj_j"]} | CMF={ind["cmf"]}')
    print(f'  MA5={ind["ma5"]} MA20={ind["ma20"]} | {ind["ma_trend"]} | 5日 {ind["change_5d"]:+.1f}% | 量比={ind["vol_ratio"]}')

    sigs = []
    if ind["golden_cross"]: sigs.append("金叉")
    if ind["death_cross"]: sigs.append("死叉")
    if ind["macd_div"]: sigs.append("MACD底背离")
    if not sigs: sigs.append("无特殊信号")
    print(f'  信号: {", ".join(sigs)}')

    print(f'\n  {"策略":<10} {"总收益%":>8} {"年化%":>8} {"最大回撤%":>8} {"夏普":>6} {"胜率%":>7} {"交易":>5}')
    print("  " + "-" * 52)
    for sname, sdata in bt.items():
        marker = " <" if sname == r["best_st"] else ""
        print(f'  {sname:<10} {sdata["total_return"]:>+8.1f} {sdata["annual_return"]:>+8.1f} {sdata["max_drawdown"]:>8.1f} {sdata["sharpe"]:>6.2f} {sdata["win_rate"]:>7.1f} {sdata["trades"]:>5}{marker}')

    # Recommendation
    if r["style"] == "趋势型":
        rec_st = "MA交叉" if bt["MA交叉"]["annual_return"] >= bt["MACD"]["annual_return"] else "MACD"
        entry = "MA5上穿MA20 + 放量确认"
    elif r["style"] == "震荡型":
        rec_st = "KDJ" if bt["KDJ"]["annual_return"] >= bt["布林带"]["annual_return"] else "布林带"
        entry = "J<20超卖金叉" if rec_st == "KDJ" else "触布林下轨+RSI<30"
    else:
        rec_st = r["best_st"]
        entry = "按各自最佳策略信号"

    action = "观望等待"
    if r["score"] >= 65: action = "[入场] 综合评分达标"
    elif r["score"] <= 40: action = "[回避] 综合评分偏低"
    elif ind["rsi"] < 30: action = "[关注] 超卖区域"
    elif ind["golden_cross"]: action = "[关注] 金叉信号"

    print(f'  推荐策略: {rec_st} | 入场条件: {entry}')
    print(f'  当前操作: {action}')

print("")
print(SEP100)
print(f"  报告结束 - {datetime.now().strftime('%Y-%m-%d %H:%M')}")
print(SEP100)
