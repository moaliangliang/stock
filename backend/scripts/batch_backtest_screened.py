#!/usr/bin/env python3
"""
批量回测 mx-xuangu 筛选结果 → 生成买入建议报告 → 发邮件
"""
import os, sys, csv, json, re, time, smtplib
from datetime import datetime, timezone
from email.mime.text import MIMEText
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("DEBUG", "true")

from app.core.database import SyncSessionLocal
from app.models.market_data import KLine, SymbolInfo
from app.services.backtest import run_backtest
from app.services.data_provider import fetch_klines_from_sina
from app.services.market import save_kline_data_sync
from app.models.strategy import StrategyType
from sqlalchemy import select, func
import numpy as np

# ── configuration ──
CSV_PATH = "/root/.openclaw/workspace/mx_data/output/mx_xuangu_市盈率百分位小于30%且市净率百分位小于30%且净利润同比增长率大于10%的A股.csv"
OUTPUT_DIR = Path("/root/.openclaw/workspace/mx_data/output")
EMAIL_TO = "maoliang84@163.com"
SMTP_HOST = "smtp.163.com"
SMTP_PORT = 465
SMTP_USER = "maoliang84@163.com"
SMTP_PASS = "MBrjp4w2eg4VdLEh"

STRATEGIES = [
    ("MA交叉(5/20)", StrategyType.MA_CROSS, {"fast_ma": 5, "slow_ma": 20}),
    ("MACD(12/26/9)", StrategyType.MACD, {"fast": 12, "slow": 26, "signal": 9}),
    ("KDJ(9/3/3)", StrategyType.KDJ, {"n": 9, "k": 3, "d": 3}),
    ("布林带(20/2)", StrategyType.BOLLINGER, {"period": 20, "std": 2.0}),
    ("趋势突破(20)", StrategyType.TREND_BREAK, {"period": 20}),
]

# ── indicator helpers ──
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

    delta_all = np.diff(closes, prepend=closes[0])
    gain = np.where(delta_all > 0, delta_all, 0)
    loss = np.where(delta_all < 0, -delta_all, 0)
    avg_gain = wilder_ema(gain, 14)[-1]
    avg_loss = wilder_ema(loss, 14)[-1]
    if not np.isnan(avg_gain) and not np.isnan(avg_loss) and avg_loss > 0:
        rsi = 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))
    else:
        rsi = 100.0 if avg_loss == 0 else 50.0

    ma5 = np.mean(closes[-5:])
    ma10 = np.mean(closes[-10:])
    ma20 = np.mean(closes[-20:])
    ma60 = np.mean(closes[-60:]) if n >= 60 else ma20
    if ma5 > ma10 > ma20:
        ma_trend = "多头"
    elif ma5 < ma10 < ma20:
        ma_trend = "空头"
    else:
        ma_trend = "震荡"

    chg_5d = (closes[-1] / closes[-6] - 1) * 100 if n >= 6 else 0
    chg_20d = (closes[-1] / closes[-21] - 1) * 100 if n >= 21 else 0
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
        cmf = float(np.sum(mf * volumes[-20:]) / np.sum(volumes[-20:]))

    ema12 = ema(closes, 12)
    ema26 = ema(closes, 26)
    macd_line = ema12 - ema26
    signal = ema(macd_line, 9)
    macd_hist = macd_line[-1] - signal[-1]

    # MACD golden cross
    golden = macd_line[-2] <= signal[-2] and macd_line[-1] > signal[-1] if n >= 2 else False
    death = macd_line[-2] >= signal[-2] and macd_line[-1] < signal[-1] if n >= 2 else False

    # MA golden/death cross
    ma_golden = False
    ma_death = False
    if n >= 21:
        ma5_prev = np.mean(closes[-7:-2])
        ma20_prev = np.mean(closes[-22:-2])
        if ma5_prev <= ma20_prev and ma5 > ma20:
            ma_golden = True
        if ma5_prev >= ma20_prev and ma5 < ma20:
            ma_death = True

    # MACD divergence
    div_bull = False
    if n >= 40:
        hist_min20 = np.min(macd_line[-20:] - signal[-20:])
        if closes[-1] < closes[-20] and (macd_line[-1] - signal[-1]) > hist_min20:
            div_bull = True

    # Bollinger
    bb_mid = ma20
    bb_std = np.std(closes[-20:])
    bb_up = bb_mid + 2 * bb_std
    bb_low = bb_mid - 2 * bb_std

    return {
        "price": round(current, 2), "rsi": round(rsi, 1),
        "ma5": round(ma5, 2), "ma10": round(ma10, 2), "ma20": round(ma20, 2), "ma60": round(ma60, 2),
        "ma_trend": ma_trend,
        "chg_5d": round(chg_5d, 1), "chg_20d": round(chg_20d, 1),
        "vol_ratio": round(vol_ratio, 2),
        "kdj_k": round(k_val, 1), "kdj_d": round(d_val, 1), "kdj_j": round(j_val, 1),
        "cmf": round(cmf, 4),
        "macd_hist": round(macd_hist, 4),
        "macd_golden": golden, "macd_death": death,
        "ma_golden": ma_golden, "ma_death": ma_death,
        "div_bull": div_bull,
        "bb_up": round(bb_up, 2), "bb_mid": round(bb_mid, 2), "bb_low": round(bb_low, 2),
    }

def classify_style(bt):
    trend = max(bt["MA交叉(5/20)"]["annual_return"], bt["MACD(12/26/9)"]["annual_return"])
    reversal = max(bt["KDJ(9/3/3)"]["annual_return"], bt["布林带(20/2)"]["annual_return"])
    if trend > reversal + 5:
        return "趋势型"
    elif reversal > trend + 5:
        return "震荡型"
    return "混合型"

def make_symbol(code: str) -> str:
    """Convert 600089 or 300750 to 600089.SH / 300750.SZ"""
    code = code.strip()
    if "." in code:
        return code
    if code.startswith(("6", "9")):
        return f"{code}.SH"
    return f"{code}.SZ"

def read_csv_codes(path: str) -> list:
    codes = []
    with open(path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            code = row.get("代码", "").strip()
            name = row.get("名称", "").strip()
            if code and code.isdigit() and len(code) == 6:
                codes.append((code, name))
    return codes


# ── main ──
print("=" * 80)
print("  批量回测 — mx-xuangu 筛选结果")
print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
print("=" * 80)

stock_list = read_csv_codes(CSV_PATH)
print(f"读取 {len(stock_list)} 只候选股票")

db = SyncSessionLocal()
results = []
errors = []

for i, (code, name) in enumerate(stock_list):
    symbol = make_symbol(code)
    try:
        rows = db.execute(
            select(KLine).where(KLine.symbol == symbol, KLine.interval == "1d")
            .order_by(KLine.timestamp.asc())
        ).scalars().all()

        if len(rows) < 60:
            # Try fetching from Sina
            try:
                raw = fetch_klines_from_sina(symbol, "1d")
                if raw:
                    sym_obj = db.execute(
                        select(SymbolInfo).where(SymbolInfo.symbol == symbol)
                    ).scalars().first()
                    if not sym_obj:
                        sym_obj = SymbolInfo(symbol=symbol, name=name, exchange="SH" if ".SH" in symbol else "SZ",
                                            asset_type="stock", status="active")
                        db.add(sym_obj)
                        db.commit()
                        db.refresh(sym_obj)
                    save_kline_data_sync(db, symbol, "1d", raw)
                    rows = db.execute(
                        select(KLine).where(KLine.symbol == symbol, KLine.interval == "1d")
                        .order_by(KLine.timestamp.asc())
                    ).scalars().all()
            except Exception:
                pass

        if len(rows) < 60:
            errors.append(f"SKIP {symbol} {name}: only {len(rows)} klines")
            continue

        klines = [{"timestamp": int(r.timestamp.timestamp()), "open": r.open, "high": r.high,
                   "low": r.low, "close": r.close, "volume": r.volume} for r in rows]

        ind = compute_indicators(klines)
        bt = {}
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
        style = classify_style(bt)
        best = max(bt.items(), key=lambda x: x[1]["annual_return"])

        # Scoring
        score = 50
        if ind["ma_trend"] == "多头": score += 10
        elif ind["ma_trend"] == "空头": score -= 8
        if ind["rsi"] < 30: score += 8
        elif ind["rsi"] > 70: score -= 5
        if ind["div_bull"]: score += 10
        if ind["macd_golden"]: score += 6
        if ind["macd_death"]: score -= 8
        if ind["ma_golden"]: score += 8
        if ind["ma_death"]: score -= 8
        if ind["cmf"] > 0.1: score += 5
        elif ind["cmf"] < -0.1: score -= 5
        if ind["kdj_j"] < 20: score += 6
        if best[1]["annual_return"] > 30: score += 8
        elif best[1]["annual_return"] > 15: score += 4
        if best[1]["sharpe"] > 1.0: score += 5

        results.append({
            "symbol": symbol, "name": name, "code": code,
            "ind": ind, "bt": bt, "style": style,
            "best_st": best[0], "best_annual": best[1]["annual_return"],
            "best_sharpe": best[1]["sharpe"],
            "score": score, "bars": len(rows),
        })
        print(f"[{i+1}/{len(stock_list)}] {symbol} {name}  bars={len(rows)}  {style}  best={best[0]}={best[1]['annual_return']:+.1f}%  score={score}")

    except Exception as e:
        errors.append(f"ERR {symbol} {name}: {e}")
        continue

db.close()

# Sort by score desc
results.sort(key=lambda x: x["score"], reverse=True)

# ── Generate report ──
now = datetime.now().strftime("%Y-%m-%d %H:%M")
report_path = OUTPUT_DIR / f"批量回测_买入建议_{datetime.now().strftime('%Y%m%d_%H%M')}.txt"

lines = []
SEP = "=" * 90
lines.append(SEP)
lines.append(f"  A股量化筛选 + 批量回测 + 买入建议报告")
lines.append(f"  生成时间: {now}")
lines.append(f"  筛选条件: PE百分位<30% + PB百分位<30% + 净利润同比增长>10%")
lines.append(f"  回测策略: MA交叉 / MACD / KDJ / 布林带 / 趋势突破")
lines.append(f"  初始资金: 10万 | 手续费: 0.03%")
lines.append(SEP)

# ── Top picks for tomorrow ──
lines.append("")
lines.append("  【明日买入建议 — Top 20】")
lines.append("  " + "-" * 86)
lines.append(f'  {"排名":<4} {"代码":<12} {"名称":<8} {"评分":>4} {"现价":>8} {"RSI":>5} {"J":>6} {"趋势":<4} {"5日%":>7} {"风格":<6} {"最佳策略":<12} {"年化%":>7} {"夏普":>5}')
lines.append("  " + "-" * 86)

buy_candidates = []
hold_candidates = []
avoid_candidates = []

for r in results:
    ind = r["ind"]
    bt = r["bt"]
    score = r["score"]

    # Decision logic
    signals = []
    if ind["macd_golden"]: signals.append("MACD金叉")
    if ind["ma_golden"]: signals.append("MA金叉")
    if ind["div_bull"]: signals.append("MACD底背离")
    if ind["rsi"] < 30: signals.append(f"RSI超卖({ind['rsi']})")
    if ind["kdj_j"] < 20: signals.append(f"KDJ超卖(J={ind['kdj_j']})")
    if ind["cmf"] > 0.1: signals.append("资金流入")
    if ind["ma_trend"] == "多头": signals.append("多头排列")

    warnings = []
    if ind["macd_death"]: warnings.append("MACD死叉")
    if ind["ma_death"]: warnings.append("MA死叉")
    if ind["cmf"] < -0.1: warnings.append(f"资金流出({ind['cmf']})")
    if ind["ma_trend"] == "空头": warnings.append("空头排列")

    if score >= 65:
        action = "BUY"
    elif score >= 50:
        action = "HOLD"
    else:
        action = "AVOID"

    if action == "BUY":
        buy_candidates.append(r)
    elif action == "HOLD":
        hold_candidates.append(r)
    else:
        avoid_candidates.append(r)

# Print top 20 by score
for rank, r in enumerate(results[:20], 1):
    ind = r["ind"]
    lines.append(f'  {rank:<4} {r["symbol"]:<12} {r["name"]:<8} {r["score"]:>4} {ind["price"]:>8.2f} {ind["rsi"]:>5.1f} {ind["kdj_j"]:>6.1f} {ind["ma_trend"]:<4} {ind["chg_5d"]:>+6.1f}% {r["style"]:<6} {r["best_st"]:<12} {r["best_annual"]:>+6.1f}% {r["best_sharpe"]:>5.2f}')

lines.append("")
lines.append(f"  📊 统计: BUY {len(buy_candidates)}只 | HOLD {len(hold_candidates)}只 | AVOID {len(avoid_candidates)}只")

# ── Buy candidates detail ──
if buy_candidates:
    lines.append("")
    lines.append(f"  【🔥 建议买入 — {len(buy_candidates)}只 (评分≥65)】")
    for r in buy_candidates:
        ind = r["ind"]
        bt = r["bt"]
        lines.append(f'\n  {"─"*80}')
        lines.append(f'  {r["symbol"]} {r["name"]} | 评分:{r["score"]} | {r["style"]} | 数据:{r["bars"]}条')
        lines.append(f'  {"─"*80}')
        lines.append(f'  现价={ind["price"]} | RSI={ind["rsi"]} | K={ind["kdj_k"]} D={ind["kdj_d"]} J={ind["kdj_j"]}')
        lines.append(f'  MA5={ind["ma5"]} MA10={ind["ma10"]} MA20={ind["ma20"]} MA60={ind["ma60"]} | {ind["ma_trend"]}')
        lines.append(f'  布林: 上={ind["bb_up"]} 中={ind["bb_mid"]} 下={ind["bb_low"]}')
        lines.append(f'  5日 {ind["chg_5d"]:+.1f}% | 20日 {ind["chg_20d"]:+.1f}% | 量比={ind["vol_ratio"]} | CMF={ind["cmf"]}')
        sigs = []
        if ind["macd_golden"]: sigs.append("MACD金叉✓")
        if ind["macd_death"]: sigs.append("MACD死叉✗")
        if ind["ma_golden"]: sigs.append("MA金叉✓")
        if ind["ma_death"]: sigs.append("MA死叉✗")
        if ind["div_bull"]: sigs.append("MACD底背离✓")
        lines.append(f'  信号: {", ".join(sigs) if sigs else "无特殊信号"}')
        lines.append(f'')
        lines.append(f'  {"策略":<14} {"总收益%":>8} {"年化%":>8} {"最大回撤%":>8} {"夏普":>6} {"胜率%":>7} {"交易":>5}')
        lines.append(f'  {"-"*52}')
        for sname, sdata in bt.items():
            marker = " ★" if sname == r["best_st"] else ""
            lines.append(f'  {sname:<14} {sdata["total_return"]:>+8.1f} {sdata["annual_return"]:>+8.1f} {sdata["max_drawdown"]:>8.1f} {sdata["sharpe"]:>6.2f} {sdata["win_rate"]:>7.1f} {sdata["trades"]:>5}{marker}')

        if r["style"] == "趋势型":
            rec = "MA交叉" if bt["MA交叉(5/20)"]["annual_return"] >= bt["MACD(12/26/9)"]["annual_return"] else "MACD"
        elif r["style"] == "震荡型":
            rec = "KDJ" if bt["KDJ(9/3/3)"]["annual_return"] >= bt["布林带(20/2)"]["annual_return"] else "布林带"
        else:
            rec = r["best_st"]
        lines.append(f'  推荐策略: {rec} | 操作: ✅ 买入')

# ── Top 5 buy recommendations ──
lines.append("")
lines.append("  【🎯 明日重点推荐 TOP 5】")
lines.append("  " + "-" * 80)
top5 = [r for r in results if r["score"] >= 60][:5]
if not top5:
    top5 = results[:5]
for i, r in enumerate(top5, 1):
    ind = r["ind"]
    bt = r["bt"]
    reasons = []
    if ind["rsi"] < 30: reasons.append(f"RSI超卖({ind['rsi']})")
    if ind["div_bull"]: reasons.append("MACD底背离")
    if ind["macd_golden"]: reasons.append("MACD金叉")
    if ind["ma_golden"]: reasons.append("MA金叉")
    if ind["kdj_j"] < 20: reasons.append(f"KDJ超卖(J={ind['kdj_j']})")
    if ind["cmf"] > 0.1: reasons.append("资金流入")
    if ind["ma_trend"] == "多头": reasons.append("多头趋势")
    if not reasons:
        reasons.append(f"综合评分{ind['rsi']} + 高回测收益")

    # Find best strategy
    best_strat = max(bt.items(), key=lambda x: x[1]["sharpe"])

    lines.append(f"  {i}. {r['symbol']} {r['name']}  现价{ind['price']}  评分{r['score']}")
    lines.append(f"     理由: {'; '.join(reasons)}")
    lines.append(f"     最佳策略: {r['best_st']} (年化{r['best_annual']:+.1f}%, 夏普{r['best_sharpe']:.2f})")
    lines.append(f"     风控: 回撤{best_strat[1]['max_drawdown']:.1f}% | 止损-8% | 持仓≤20%")
    lines.append("")

# ── Full ranking ──
lines.append("")
lines.append(f"  【完整排名 — {len(results)}只】")
lines.append("  " + "-" * 86)
lines.append(f'  {"排名":<4} {"代码":<12} {"名称":<8} {"评分":>4} {"现价":>8} {"RSI":>5} {"J":>6} {"趋势":<4} {"5日%":>7} {"操作":<6} {"最佳策略":<12} {"年化%":>7}')
lines.append("  " + "-" * 86)
for rank, r in enumerate(results, 1):
    ind = r["ind"]
    if r["score"] >= 65:
        action = "✅买入"
    elif r["score"] >= 50:
        action = "⏸观望"
    else:
        action = "❌回避"
    lines.append(f'  {rank:<4} {r["symbol"]:<12} {r["name"]:<8} {r["score"]:>4} {ind["price"]:>8.2f} {ind["rsi"]:>5.1f} {ind["kdj_j"]:>6.1f} {ind["ma_trend"]:<4} {ind["chg_5d"]:>+6.1f}% {action:<6} {r["best_st"]:<12} {r["best_annual"]:>+6.1f}%')

if errors:
    lines.append("")
    lines.append(f"  【处理异常 — {len(errors)}条】")
    for e in errors:
        lines.append(f"  - {e}")

lines.append("")
lines.append(SEP)
lines.append("  免责声明：本报告由量化系统自动生成，仅供参考，不构成投资建议。投资有风险，入市需谨慎。")
lines.append(SEP)

report = "\n".join(lines)
report_path.write_text(report, encoding="utf-8")
print(f"\n报告已保存: {report_path}")

# ── Email ──
print("发送邮件...")
msg = MIMEText(report, "plain", "utf-8")
msg["Subject"] = f"A股量化筛选+批量回测 买入建议报告 {datetime.now().strftime('%Y%m%d')}"
msg["From"] = SMTP_USER
msg["To"] = EMAIL_TO
with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as smtp:
    smtp.login(SMTP_USER, SMTP_PASS)
    smtp.send_message(msg)
print("邮件已发送")

print("\nDone.")
