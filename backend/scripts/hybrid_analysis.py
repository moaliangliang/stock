#!/usr/bin/env python3
"""
混合分析: 筛选股技术指标评分 + 可选回测 → 买入建议 → 发邮件
对53+条K线的股票计算技术指标和评分，对120+条的额外跑回测
"""
import os, sys, csv, smtplib, re
from datetime import datetime
from email.mime.text import MIMEText
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["DEBUG"] = "true"

# silence sqlalchemy logs
import logging
for name in ['sqlalchemy', 'sqlalchemy.engine', 'sqlalchemy.engine.Engine']:
    logging.getLogger(name).setLevel(logging.ERROR)

from app.core.database import SyncSessionLocal
from app.models.market_data import KLine, SymbolInfo
from app.services.backtest import run_backtest
from app.models.strategy import StrategyType
from sqlalchemy import select, func
import numpy as np

CSV_PATH = "/root/.openclaw/workspace/mx_data/output/mx_xuangu_市盈率百分位小于30%且市净率百分位小于30%且净利润同比增长率大于10%的A股.csv"
OUTPUT_DIR = Path("/root/.openclaw/workspace/mx_data/output")
EMAIL_TO = "maoliang84@163.com"
SMTP_HOST = "smtp.163.com"; SMTP_PORT = 465
SMTP_USER = "maoliang84@163.com"; SMTP_PASS = "MBrjp4w2eg4VdLEh"

STRATEGIES = [
    ("MA交叉(5/20)", StrategyType.MA_CROSS, {"fast_ma": 5, "slow_ma": 20}),
    ("MACD(12/26/9)", StrategyType.MACD, {"fast": 12, "slow": 26, "signal": 9}),
    ("KDJ(9/3/3)", StrategyType.KDJ, {"n": 9, "k": 3, "d": 3}),
    ("布林带(20/2)", StrategyType.BOLLINGER, {"period": 20, "std": 2.0}),
    ("趋势突破(20)", StrategyType.TREND_BREAK, {"period": 20}),
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
        if not np.isnan(arr[i]) and not np.isnan(out[i-1]):
            out[i] = alpha * float(arr[i]) + (1 - alpha) * out[i-1]
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

    # RSI(14) Wilder EMA
    delta = np.diff(closes, prepend=closes[0])
    gain = np.where(delta > 0, delta, 0)
    loss = np.where(delta < 0, -delta, 0)
    ag = wilder_ema(gain, 14)[-1]
    al = wilder_ema(loss, 14)[-1]
    rsi = 100 - 100/(1 + ag/al) if not np.isnan(ag) and not np.isnan(al) and al > 0 else (100 if al == 0 else 50)

    ma5 = np.mean(closes[-5:])
    ma10 = np.mean(closes[-10:])
    ma20 = np.mean(closes[-20:])
    ma60 = np.mean(closes[-60:]) if n >= 60 else ma20
    if ma5 > ma10 > ma20: ma_trend = "多头"
    elif ma5 < ma10 < ma20: ma_trend = "空头"
    else: ma_trend = "震荡"

    chg_5d = (closes[-1] / closes[-6] - 1) * 100 if n >= 6 else 0
    chg_20d = (closes[-1] / closes[-21] - 1) * 100 if n >= 21 else 0
    vr_5_20 = np.mean(volumes[-5:]) / np.mean(volumes[-20:]) if n >= 20 else 1

    # KDJ
    low_n = np.min(lows[-9:]); high_n = np.max(highs[-9:])
    rsv = (current - low_n) / (high_n - low_n) * 100 if high_n > low_n else 50
    k_val = rsv * 1/3 + 50 * 2/3
    d_val = k_val * 1/3 + 50 * 2/3
    j_val = 3 * k_val - 2 * d_val

    # CMF
    cmf = 0
    if n >= 20:
        mf = ((closes[-20:] - lows[-20:]) - (highs[-20:] - closes[-20:])) / (highs[-20:] - lows[-20:] + 1e-9)
        cmf = float(np.sum(mf * volumes[-20:]) / np.sum(volumes[-20:]))

    # MACD
    e12 = ema(closes, 12); e26 = ema(closes, 26)
    macd_line = e12 - e26; sig = ema(macd_line, 9)
    macd_hist = macd_line[-1] - sig[-1]
    macd_golden = macd_line[-2] <= sig[-2] and macd_line[-1] > sig[-1] if n >= 2 else False
    macd_death = macd_line[-2] >= sig[-2] and macd_line[-1] < sig[-1] if n >= 2 else False

    # MA cross
    ma_golden = False; ma_death = False
    if n >= 21:
        ma5p = np.mean(closes[-7:-2]); ma20p = np.mean(closes[-22:-2])
        if ma5p <= ma20p and ma5 > ma20: ma_golden = True
        if ma5p >= ma20p and ma5 < ma20: ma_death = True

    # MACD divergence
    div_bull = False
    if n >= 40:
        hist_20 = macd_line[-20:] - sig[-20:]
        if closes[-1] < closes[-20] and (macd_line[-1] - sig[-1]) > np.min(hist_20):
            div_bull = True

    # Bollinger
    bb_mid = ma20; bb_std = np.std(closes[-20:])
    bb_up, bb_low = bb_mid + 2*bb_std, bb_mid - 2*bb_std

    return {
        "price": round(current, 2), "rsi": round(rsi, 1),
        "ma5": round(ma5, 2), "ma10": round(ma10, 2), "ma20": round(ma20, 2), "ma60": round(ma60, 2),
        "ma_trend": ma_trend, "chg_5d": round(chg_5d, 1), "chg_20d": round(chg_20d, 1),
        "vol_ratio": round(vr_5_20, 2), "kdj_k": round(k_val, 1), "kdj_d": round(d_val, 1), "kdj_j": round(j_val, 1),
        "cmf": round(cmf, 4), "macd_hist": round(macd_hist, 4),
        "macd_golden": macd_golden, "macd_death": macd_death,
        "ma_golden": ma_golden, "ma_death": ma_death,
        "div_bull": div_bull, "bb_up": round(bb_up, 2), "bb_mid": round(bb_mid, 2), "bb_low": round(bb_low, 2),
    }

def score_stock(ind, bt_results=None):
    s = 50
    if ind["ma_trend"] == "多头": s += 10
    elif ind["ma_trend"] == "空头": s -= 8
    if ind["rsi"] < 30: s += 8
    elif ind["rsi"] > 70: s -= 5
    if ind["div_bull"]: s += 10
    if ind["macd_golden"]: s += 6
    if ind["macd_death"]: s -= 8
    if ind["ma_golden"]: s += 8
    if ind["ma_death"]: s -= 8
    if ind["cmf"] > 0.1: s += 5
    elif ind["cmf"] < -0.1: s -= 5
    if ind["kdj_j"] < 20: s += 6
    if bt_results:
        best_ann = max(bt_results.values(), key=lambda x: x["annual_return"])
        if best_ann["annual_return"] > 30: s += 8
        elif best_ann["annual_return"] > 15: s += 4
        if best_ann["sharpe"] > 1.0: s += 5
    return max(0, min(100, s))

def make_symbol(code):
    code = code.strip()
    if "." in code: return code
    return f"{code}.SH" if code.startswith(("6", "9")) else f"{code}.SZ"

def classify_style(bt):
    trend = max(bt["MA交叉(5/20)"]["annual_return"], bt["MACD(12/26/9)"]["annual_return"])
    reversal = max(bt["KDJ(9/3/3)"]["annual_return"], bt["布林带(20/2)"]["annual_return"])
    if trend > reversal + 5: return "趋势型"
    elif reversal > trend + 5: return "震荡型"
    return "混合型"

# ── main ──
print("=" * 80)
print("  A股量化筛选 + 混合分析(指标+回测) + 买入建议")
print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
print("=" * 80)

with open(CSV_PATH, encoding="utf-8-sig") as f:
    reader = csv.DictReader(f)
    stocks = [(row["代码"].strip(), row["名称"].strip()) for row in reader if row["代码"].strip().isdigit() and len(row["代码"].strip()) == 6]
print(f"筛选池: {len(stocks)}只")

db = SyncSessionLocal()
results = []
bt_count = 0

for i, (code, name) in enumerate(stocks):
    symbol = make_symbol(code)
    rows = db.execute(
        select(KLine).where(KLine.symbol == symbol, KLine.interval == "1d").order_by(KLine.timestamp.asc())
    ).scalars().all()

    if len(rows) < 20:
        continue

    klines = [{"timestamp": int(r.timestamp.timestamp()), "open": r.open, "high": r.high,
               "low": r.low, "close": r.close, "volume": r.volume} for r in rows]
    ind = compute_indicators(klines)

    bt = {}
    if len(rows) >= 120:
        try:
            for sname, stype, params in STRATEGIES:
                r = run_backtest(stype, params, klines)
                bt[sname] = {
                    "total_return": round(r["total_return"] * 100, 1),
                    "annual_return": round(r["annual_return"] * 100, 1),
                    "max_drawdown": round(r["max_drawdown"] * 100, 1),
                    "sharpe": round(r.get("sharpe_ratio", 0), 2),
                    "win_rate": round(r.get("win_rate", 0) * 100, 1),
                    "trades": r.get("total_trades", 0),
                }
            bt_count += 1
        except Exception:
            pass

    style = classify_style(bt) if bt else "-"
    score = score_stock(ind, bt if bt else None)
    best = max(bt.items(), key=lambda x: x[1]["annual_return"]) if bt else None

    results.append({
        "symbol": symbol, "name": name, "code": code, "ind": ind, "bt": bt,
        "style": style, "score": score, "bars": len(rows),
        "best_st": best[0] if best else "-",
        "best_annual": best[1]["annual_return"] if best else 0,
        "best_sharpe": best[1]["sharpe"] if best else 0,
    })

db.close()
results.sort(key=lambda x: x["score"], reverse=True)
print(f"分析: {len(results)}只 (含回测{bt_count}只)")

# ── Report ──
now = datetime.now().strftime("%Y-%m-%d %H:%M"); fdate = datetime.now().strftime("%Y%m%d_%H%M")
report_path = OUTPUT_DIR / f"筛选回测_买入建议_{fdate}.txt"
L = []; S = "-" * 100

L.append(S)
L.append(f"  A股量化筛选回测 + 明日买入建议")
L.append(f"  生成: {now} | 筛选: PE百分位<30% + PB百分位<30% + 净利增长>10%")
L.append(f"  池: {len(stocks)}只 | 技术分析: {len(results)}只 | 含回测: {bt_count}只")
L.append(S)

# Top picks
buy = [r for r in results if r["score"] >= 60]
hold = [r for r in results if 45 <= r["score"] < 60]
avoid = [r for r in results if r["score"] < 45]

L.append("")
L.append(f"  【🔥 建议买入 (评分≥60): {len(buy)}只】")
L.append(f"  【⏸ 观望 (45-59): {len(hold)}只】")
L.append(f"  【❌ 回避 (<45): {len(avoid)}只】")

L.append("")
L.append(f"  【📊 买入候选 Top 20】")
L.append("  " + "-" * 95)
L.append(f'  {"排名":<4} {"代码":<12} {"名称":<8} {"评分":>4} {"现价":>8} {"RSI":>5} {"J":>6} {"趋势":<4} {"5日%":>7} {"CMF":>7} {"回测":>4} {"最佳策略":<12} {"年化%":>7}')
L.append("  " + "-" * 95)
for rank, r in enumerate(results[:20], 1):
    ind = r["ind"]
    has_bt = "✓" if r["bt"] else "-"
    L.append(f'  {rank:<4} {r["symbol"]:<12} {r["name"]:<8} {r["score"]:>4} {ind["price"]:>8.2f} {ind["rsi"]:>5.1f} {ind["kdj_j"]:>6.1f} {ind["ma_trend"]:<4} {ind["chg_5d"]:>+6.1f}% {ind["cmf"]:>7.3f} {has_bt:>4} {r["best_st"]:<12} {r["best_annual"]:>+6.1f}%')

# Buy detail
if buy:
    L.append("")
    L.append(f"  【🔥 建议买入详情 — {len(buy)}只】")
    for rank, r in enumerate(buy, 1):
        ind = r["ind"]; bt = r["bt"]
        L.append(f'\n  {"─"*80}')
        L.append(f'  #{rank} {r["symbol"]} {r["name"]} | 评分:{r["score"]} | {r["style"]} | K线:{r["bars"]}条')
        L.append(f'  {"─"*80}')
        L.append(f'  现价={ind["price"]} | RSI={ind["rsi"]} | K={ind["kdj_k"]} D={ind["kdj_d"]} J={ind["kdj_j"]}')
        L.append(f'  MA5={ind["ma5"]} MA10={ind["ma10"]} MA20={ind["ma20"]} MA60={ind["ma60"]} | {ind["ma_trend"]}')
        L.append(f'  布林: 上={ind["bb_up"]} 中={ind["bb_mid"]} 下={ind["bb_low"]} | CMF={ind["cmf"]}')
        L.append(f'  5日 {ind["chg_5d"]:+.1f}% | 20日 {ind["chg_20d"]:+.1f}% | 量比={ind["vol_ratio"]}')

        reasons = []
        if ind["rsi"] < 30: reasons.append(f"RSI超卖({ind['rsi']})")
        if ind["div_bull"]: reasons.append("MACD底背离")
        if ind["macd_golden"]: reasons.append("MACD金叉")
        if ind["ma_golden"]: reasons.append("MA金叉")
        if ind["kdj_j"] < 20: reasons.append(f"KDJ超卖(J={ind['kdj_j']})")
        if ind["cmf"] > 0.1: reasons.append("资金流入")
        if ind["ma_trend"] == "多头": reasons.append("多头排列")
        if not reasons: reasons.append("综合评分达标")
        L.append(f'  买入理由: {"; ".join(reasons)}')

        if bt:
            L.append(f'')
            L.append(f'  {"策略":<14} {"总收益%":>8} {"年化%":>8} {"最大回撤%":>8} {"夏普":>6} {"胜率%":>7} {"交易":>5}')
            L.append(f'  {"-"*52}')
            for sname, sdata in bt.items():
                marker = " ★" if sname == r["best_st"] else ""
                L.append(f'  {sname:<14} {sdata["total_return"]:>+8.1f} {sdata["annual_return"]:>+8.1f} {sdata["max_drawdown"]:>8.1f} {sdata["sharpe"]:>6.2f} {sdata["win_rate"]:>7.1f} {sdata["trades"]:>5}{marker}')

            if r["style"] == "趋势型":
                rec = "MA交叉" if bt.get("MA交叉(5/20)", {}).get("annual_return", 0) >= bt.get("MACD(12/26/9)", {}).get("annual_return", 0) else "MACD"
            elif r["style"] == "震荡型":
                rec = "KDJ" if bt.get("KDJ(9/3/3)", {}).get("annual_return", 0) >= bt.get("布林带(20/2)", {}).get("annual_return", 0) else "布林带"
            else:
                rec = r["best_st"]
            L.append(f'  推荐策略: {rec}')
        else:
            L.append(f'  (K线不足120条，跳过回测)')

        L.append(f'  操作建议: ✅ 可买入 | 止损-8% | 仓位≤20%')

# Top 5
L.append("")
L.append(f"  【🎯 明日重点推荐 TOP 5】")
L.append("  " + "-" * 80)
for i, r in enumerate(results[:5], 1):
    ind = r["ind"]
    reasons = []
    if ind["rsi"] < 30: reasons.append(f"RSI超卖({ind['rsi']})")
    if ind["div_bull"]: reasons.append("MACD底背离")
    if ind["macd_golden"]: reasons.append("MACD金叉")
    if ind["ma_golden"]: reasons.append("MA金叉")
    if ind["kdj_j"] < 20: reasons.append(f"KDJ超卖(J={ind['kdj_j']})")
    if ind["cmf"] > 0.1: reasons.append("资金流入")
    if ind["ma_trend"] == "多头": reasons.append("多头趋势")
    if not reasons: reasons.append(f"评分{r['score']} + 综合信号偏多")
    L.append(f"  {i}. {r['symbol']} {r['name']}  现价{ind['price']}  评分{r['score']}")
    L.append(f"     理由: {'; '.join(reasons)}")
    L.append(f"     策略: {r['best_st']} (年化{r['best_annual']:+.1f}%)" if r["bt"] else f"     指标信号: RSI={ind['rsi']}, J={ind['kdj_j']}, {ind['ma_trend']}")

# Full ranking
L.append("")
L.append(f"  【完整排名 — {len(results)}只】")
L.append("  " + "-" * 85)
L.append(f'  {"排名":<4} {"代码":<12} {"名称":<8} {"评分":>4} {"现价":>8} {"RSI":>5} {"J":>6} {"趋势":<4} {"5日%":>7} {"建议":<6} {"策略":<12} {"年化%":>7}')
L.append("  " + "-" * 85)
for rank, r in enumerate(results, 1):
    ind = r["ind"]
    if r["score"] >= 60: action = "✅买入"
    elif r["score"] >= 45: action = "⏸观望"
    else: action = "❌回避"
    L.append(f'  {rank:<4} {r["symbol"]:<12} {r["name"]:<8} {r["score"]:>4} {ind["price"]:>8.2f} {ind["rsi"]:>5.1f} {ind["kdj_j"]:>6.1f} {ind["ma_trend"]:<4} {ind["chg_5d"]:>+6.1f}% {action:<6} {r["best_st"]:<12} {r["best_annual"]:>+6.1f}%')

L.append("")
L.append(S)
L.append("  免责声明：量化系统自动生成，仅供参考，不构成投资建议。投资有风险，入市需谨慎。")
L.append("  筛选条件: PE百分位<30% + PB百分位<30% + 净利增长>10% | 数据: 东方财富/新浪财经")
L.append(S)

report = "\n".join(L)
report_path.write_text(report, encoding="utf-8")
print(f"报告: {report_path}")

# ── Auto-Trade (opt-in via AUTO_TRADE=1) ──
if os.environ.get("AUTO_TRADE", "").lower() in ("1", "true", "yes") and buy:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    try:
        from app.services.analysis_bridge import submit_buy_recommendations
        buy_recs = []
        for r in buy:
            ind = r["ind"]
            reasons = []
            if ind["rsi"] < 30: reasons.append(f"RSI超卖({ind['rsi']})")
            if ind["div_bull"]: reasons.append("MACD底背离")
            if ind["macd_golden"]: reasons.append("MACD金叉")
            if ind["ma_golden"]: reasons.append("MA金叉")
            if ind["kdj_j"] < 20: reasons.append(f"KDJ超卖(J={ind['kdj_j']})")
            if ind["cmf"] > 0.1: reasons.append("资金流入")
            if ind["ma_trend"] == "多头": reasons.append("多头排列")
            if not reasons: reasons.append(f"综合评分{r['score']}")
            buy_recs.append({
                "symbol": r["symbol"], "name": r["name"], "price": ind["price"],
                "score": r["score"], "reasons": reasons,
            })
        print(f"\n[TRADE] 提交 {len(buy_recs)} 只买入候选到自动交易...")
        results = submit_buy_recommendations(buy_recs)
        for res in results:
            at = res.get("auto_trade", {})
            if at.get("executed"):
                print(f"  ✅ {res['symbol']}: {at.get('reason', 'OK')}")
            else:
                print(f"  ⏭ {res['symbol']}: {at.get('reason', 'SKIP')}")
    except Exception as e:
        print(f"[TRADE] 自动交易失败: {e}")

# Email
print("发送邮件...")
msg = MIMEText(report, "plain", "utf-8")
msg["Subject"] = f"量化筛选+回测 明日买入建议 {datetime.now().strftime('%Y%m%d')}"
msg["From"] = SMTP_USER; msg["To"] = EMAIL_TO
with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as smtp:
    smtp.login(SMTP_USER, SMTP_PASS)
    smtp.send_message(msg)
print("邮件已发送 ✓")
print("Done.")
