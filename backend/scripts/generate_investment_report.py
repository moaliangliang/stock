"""
批量投资建议报告 — 对所有活跃标的生成分析报告，输出到文件。

用法:
    DEBUG=true python scripts/generate_investment_report.py
"""
import json
import os
import sys
from collections import OrderedDict

os.environ.setdefault("DEBUG", "true")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
from sqlalchemy import select, text

from app.core.database import SyncSessionLocal
from app.models.market_data import KLine, SymbolInfo
from app.services.backtest import run_backtest
from app.models.strategy import StrategyType
from app.models.backtest import BacktestResult

OUTPUT = os.path.join(os.path.dirname(__file__), "..", "..", "investment_report.md")


def compute_indicators(klines):
    closes = np.array([k["close"] for k in klines])
    highs = np.array([k["high"] for k in klines])
    lows = np.array([k["low"] for k in klines])
    volumes = np.array([k["volume"] for k in klines])
    n = len(closes)

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

    current_price = closes[-1]
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
    if ma5 > ma20 > ma60:
        ma_trend = "多头"
    elif ma5 < ma20 < ma60:
        ma_trend = "空头"
    else:
        ma_trend = "震荡"

    change_5d = (closes[-1] / closes[-6] - 1) * 100 if n >= 6 else 0
    vol_ratio = np.mean(volumes[-5:]) / np.mean(volumes[-20:]) if n >= 20 else 1

    # Standard recursive KDJ (9,3,3) — matches Eastmoney / Tongdaxin
    k_arr = np.full(n, 50.0)
    d_arr = np.full(n, 50.0)
    for i in range(n):
        start = max(0, i - 8)
        low_i = np.min(lows[start:i+1])
        high_i = np.max(highs[start:i+1])
        rsv_i = (closes[i] - low_i) / (high_i - low_i) * 100 if high_i > low_i else 50.0
        k_arr[i] = 2.0/3.0 * k_arr[i-1] + 1.0/3.0 * rsv_i
        d_arr[i] = 2.0/3.0 * d_arr[i-1] + 1.0/3.0 * k_arr[i]

    k_val = k_arr[-1]
    d_val = d_arr[-1]
    j_val = 3 * k_val - 2 * d_val
    k_prev = k_arr[-2]
    d_prev = d_arr[-2]
    j_prev = 3 * k_prev - 2 * d_prev

    signals = []
    if n >= 21:
        ma5_prev = np.mean(closes[-7:-2])
        ma20_prev = np.mean(closes[-22:-2])
        if ma5_prev <= ma20_prev and ma5 > ma20:
            signals.append("MA金叉 ↑")
        if ma5_prev >= ma20_prev and ma5 < ma20:
            signals.append("MA死叉 ↓")

    # KDJ crossover signals (K/D line crossovers)
    if k_prev <= d_prev and k_val > d_val:
        signals.append("KDJ金叉 ↑")
    if k_prev >= d_prev and k_val < d_val:
        signals.append("KDJ死叉 ↓")

    return {
        "price": round(current_price, 2),
        "rsi": round(rsi, 1),
        "j": round(j_val, 1),
        "j_prev": round(j_prev, 1),
        "k": round(k_val, 1),
        "d": round(d_val, 1),
        "ma5": round(ma5, 2),
        "ma20": round(ma20, 2),
        "trend": ma_trend,
        "chg5d": round(change_5d, 2),
        "vol_ratio": round(vol_ratio, 2),
        "signals": signals,
    }


def generate_recommendation(ind, bt_sorted):
    """Generate investment recommendation from indicators and backtest rankings.

    Signal interpretation is STYLE-AWARE:
      - 震荡型 (KDJ/Bollinger best): uses J-value position + KDJ crossovers.
        MA crossovers are NOISE for range-bound stocks — they are ignored.
      - 趋势型 (MA_CROSS/MACD best): uses MA crossovers + RSI position.
    """
    rsi = ind["rsi"]
    j = ind["j"]
    j_prev = ind["j_prev"]
    k = ind["k"]
    d = ind["d"]
    signals = ind["signals"]

    if not bt_sorted:
        return {"strategy": "-", "entry": "数据不足", "exit": "", "action": "观望",
                "style": "-", "signal_basis": ""}

    best = bt_sorted[0]
    strategy_name = best["strategy"]

    if strategy_name in ("kdj", "bollinger"):
        style = "震荡型"
        entry = "J < 20 超卖区KDJ金叉" if strategy_name == "kdj" else "价格触及布林下轨 + RSI < 30"
        exit_cond = "J > 80 超买区KDJ死叉" if strategy_name == "kdj" else "回归布林中轨"

        # For range-bound stocks, J-value position is the primary signal.
        # MA crossovers are explicitly IGNORED — they produce whipsaw losses.
        if "KDJ金叉" in str(signals) and j < 30:
            action = "✅ 买入信号"
            signal_basis = f"KDJ金叉(K↑D) @ 超卖区 J={j:.0f}"
        elif "KDJ死叉" in str(signals) and j > 70:
            action = "⚠️ 卖出信号"
            signal_basis = f"KDJ死叉(K↓D) @ 超买区 J={j:.0f}"
        elif j < 20:
            action = "👀 超卖，关注反弹"
            signal_basis = f"J={j:.0f} 进入超卖区"
        elif j > 80:
            action = "⏸ 超买，等回调"
            signal_basis = f"J={j:.0f} 进入超买区"
        elif j_prev < 20 and j >= 20:
            action = "✅ 买入信号"
            signal_basis = f"J线上穿20 (J={j_prev:.0f}→{j:.0f})"
        elif j_prev > 80 and j <= 80:
            action = "⚠️ 卖出信号"
            signal_basis = f"J线下穿80 (J={j_prev:.0f}→{j:.0f})"
        else:
            action = "⏺ 观望等待"
            signal_basis = f"J={j:.0f} 中性区，等待超买/超卖信号"
    else:
        style = "趋势型"
        entry = "MA5上穿MA20 + 放量" if strategy_name == "ma_cross" else "MACD金叉 + DIF上穿DEA"
        exit_cond = "MA5下穿MA20" if strategy_name == "ma_cross" else "MACD死叉"

        # For trending stocks, MA crossover is the primary signal
        if "MA金叉" in str(signals):
            action = "✅ 买入信号"
            signal_basis = "MA5上穿MA20 金叉"
        elif "MA死叉" in str(signals):
            action = "⚠️ 卖出信号"
            signal_basis = "MA5下穿MA20 死叉"
        elif rsi > 70:
            action = "⏸ 超买，等回调"
            signal_basis = f"RSI={rsi:.0f} 超买"
        elif rsi < 30:
            action = "👀 超卖，关注反弹"
            signal_basis = f"RSI={rsi:.0f} 超卖"
        else:
            action = "⏺ 观望等待"
            signal_basis = f"RSI={rsi:.0f} 中性，等待金叉/死叉"

    return {
        "strategy": strategy_name,
        "style": style,
        "entry": entry,
        "exit": exit_cond,
        "action": action,
        "signal_basis": signal_basis,
    }


def main():
    db = SyncSessionLocal()
    try:
        # Get all active symbols with name
        symbols = db.execute(
            select(SymbolInfo).where(SymbolInfo.status == "active").order_by(SymbolInfo.is_watched.desc(), SymbolInfo.symbol)
        ).scalars().all()
    finally:
        db.close()

    # Pre-load all backtest results from DB
    db = SyncSessionLocal()
    try:
        bt_rows = db.execute(
            select(BacktestResult).order_by(BacktestResult.annual_return.desc())
        ).scalars().all()
        bt_map = {}
        for r in bt_rows:
            bt_map.setdefault(r.symbol, []).append({
                "strategy": r.strategy_type,
                "params": r.strategy_params,
                "annual_return": r.annual_return,
                "total_return": r.total_return,
                "max_drawdown": r.max_drawdown,
                "sharpe_ratio": r.sharpe_ratio,
                "win_rate": r.win_rate,
                "total_trades": r.total_trades,
            })
    finally:
        db.close()

    lines = []
    def w(s=""):
        lines.append(s)

    dt = __import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M')

    w("# 量化交易平台 — 全股票投资建议报告")
    w()
    w(f"**生成时间**: {dt} | **标的数量**: {len(symbols)} 只")
    w()

    # ---- Signal system legend ----
    w("---")
    w()
    w("## 信号体系说明")
    w()
    w("| 标的类型 | 信号依据 | 买入条件 | 卖出条件 |")
    w("|----------|----------|----------|----------|")
    w("| **震荡型** (KDJ/Bollinger最优) | J值位置 + KDJ K/D交叉 | J<20 或 J线上穿20 | J>80 或 J线下穿80 |")
    w("| **趋势型** (MA_CROSS/MACD最优) | MA金叉/死叉 + RSI | MA5上穿MA20 | MA5下穿MA20 |")
    w()
    w("| 符号 | 含义 |")
    w("|------|------|")
    w("| ✅ 买入信号 | 适配策略触发入场条件 |")
    w("| 👀 超卖关注 | 价格处于超卖区，等待反弹确认 |")
    w("| ⏺ 观望等待 | 无明确信号，持币观望 |")
    w("| ⏸ 超买等回调 | 价格偏高，等回调再入场 |")
    w("| ⚠️ 卖出信号 | 适配策略触发离场条件 |")
    w()
    w("---")
    w()
    w("## 个股分析")
    w()

    # ---- Per-stock analysis ----
    for sym in symbols:
        symbol = sym.symbol
        name = sym.name or symbol

        db = SyncSessionLocal()
        try:
            rows = db.execute(
                select(KLine)
                .where(KLine.symbol == symbol, KLine.interval == "1d")
                .order_by(KLine.timestamp.asc())
            ).scalars().all()
        finally:
            db.close()

        klines = [
            {"timestamp": int(r.timestamp.timestamp()), "open": r.open, "high": r.high,
             "low": r.low, "close": r.close, "volume": r.volume}
            for r in rows
        ]

        if len(klines) < 60:
            w(f"### {symbol} {name}")
            w()
            w(f"> ⚠️ 数据不足 ({len(klines)}条)，跳过")
            w()
            continue

        ind = compute_indicators(klines)
        bt_list = bt_map.get(symbol, [])
        rec = generate_recommendation(ind, bt_list)

        w(f"### {symbol} {name}")
        w()

        # Indicator table
        sig_str = ", ".join(ind["signals"]) if ind["signals"] else "无"
        w("| 指标 | 数值 |")
        w("|------|------|")
        w(f"| 现价 | **{ind['price']}** |")
        w(f"| 均线趋势 | {ind['trend']} |")
        w(f"| RSI(14) | {ind['rsi']} |")
        w(f"| KDJ | K={ind['k']} D={ind['d']} J={ind['j']} |")
        w(f"| MA5 / MA20 | {ind['ma5']} / {ind['ma20']} |")
        w(f"| 5日涨跌 | {ind['chg5d']:+.1f}% |")
        w(f"| 量比(5/20) | {ind['vol_ratio']:.2f} |")
        w(f"| 技术信号 | {sig_str} |")
        w()

        # Backtest ranking table
        if bt_list:
            w("**策略排名 (年化):**")
            w()
            w("| # | 策略 | 参数 | 年化收益 | 最大回撤 | 夏普比率 |")
            w("|---|------|------|----------|----------|----------|")
            medals = ["🥇", "🥈", "🥉", "4", "5"]
            for i, bt in enumerate(bt_list[:5]):
                w(f"| {medals[i]} | {bt['strategy']} | {bt['params']} "
                  f"| {bt['annual_return']*100:+6.1f}% "
                  f"| {bt['max_drawdown']*100:5.1f}% "
                  f"| {bt['sharpe_ratio']:5.2f} |")
            w()

        # Recommendation
        w("**投资建议:**")
        w()
        w("| 维度 | 内容 |")
        w("|------|------|")
        w(f"| 风格 | {rec['style']} |")
        w(f"| 推荐策略 | {rec['strategy']} |")
        w(f"| 信号依据 | {rec.get('signal_basis', '-')} |")
        w(f"| 入场条件 | {rec['entry']} |")
        w(f"| 出场条件 | {rec['exit']} |")
        w(f"| **操作** | **{rec['action']}** |")
        w()
        w("---")
        w()

    # ---- Summary section ----
    w("## 汇总")
    w()

    # Top 10 by annual return
    if bt_map:
        best_per_stock = {}
        for sym_key, lst in bt_map.items():
            if lst:
                best_per_stock[sym_key] = lst[0]["annual_return"]
        ranked = sorted(best_per_stock.items(), key=lambda x: x[1], reverse=True)

        w("### 年化收益 Top 10")
        w()
        w("| # | 代码 | 名称 | 年化收益 |")
        w("|---|------|------|----------|")
        for i, (sym_key, ann) in enumerate(ranked[:10]):
            db3 = SyncSessionLocal()
            try:
                si = db3.execute(select(SymbolInfo).where(SymbolInfo.symbol == sym_key)).scalars().first()
                nm = si.name if si else sym_key
            finally:
                db3.close()
            w(f"| {i+1} | {sym_key} | {nm} | {ann*100:+6.1f}% |")
        w()

    # Action distribution
    db = SyncSessionLocal()
    try:
        all_syms = db.execute(
            select(SymbolInfo).where(SymbolInfo.status == "active")
        ).scalars().all()
    finally:
        db.close()

    action_counts = {"buy": 0, "oversold": 0, "hold": 0, "overbought": 0, "sell": 0}
    for sym_obj in all_syms:
        db = SyncSessionLocal()
        try:
            rows = db.execute(
                select(KLine).where(KLine.symbol == sym_obj.symbol, KLine.interval == "1d")
                .order_by(KLine.timestamp.asc())
            ).scalars().all()
        finally:
            db.close()
        if len(rows) < 60:
            continue
        kls = [{"close": r.close, "high": r.high, "low": r.low, "volume": r.volume,
                "timestamp": int(r.timestamp.timestamp()), "open": r.open} for r in rows]
        ind2 = compute_indicators(kls)
        bt2 = bt_map.get(sym_obj.symbol, [])
        rec2 = generate_recommendation(ind2, bt2)
        if "买入" in rec2["action"]:
            action_counts["buy"] += 1
        elif "超卖" in rec2["action"]:
            action_counts["oversold"] += 1
        elif "卖出" in rec2["action"]:
            action_counts["sell"] += 1
        elif "超买" in rec2["action"]:
            action_counts["overbought"] += 1
        else:
            action_counts["hold"] += 1

    w("### 操作信号分布")
    w()
    w("| 信号 | 数量 |")
    w("|------|------|")
    w(f"| ✅ 买入 | {action_counts['buy']} |")
    w(f"| 👀 超卖关注 | {action_counts['oversold']} |")
    w(f"| ⏺ 观望 | {action_counts['hold']} |")
    w(f"| ⏸ 超买 | {action_counts['overbought']} |")
    w(f"| ⚠️ 卖出 | {action_counts['sell']} |")
    w()
    w(f"> 报告生成时间: {dt}")

    # Write to file
    content = "\n".join(lines)
    with open(OUTPUT, "w", encoding="utf-8") as f:
        f.write(content)

    print(content)
    print(f"\n\n报告已保存: {OUTPUT}")
    print(f"文件大小: {os.path.getsize(OUTPUT)/1024:.1f} KB")


if __name__ == "__main__":
    main()
