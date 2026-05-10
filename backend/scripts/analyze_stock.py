#!/usr/bin/env python3
"""
单股深度分析 — 获取数据 / 因子评分 / 五策略回测 / 投资决策

用法:
  python scripts/analyze_stock.py 比亚迪
  python scripts/analyze_stock.py 002594.SZ
  python scripts/analyze_stock.py 宁德时代 --no-fetch  # 跳过拉数据
"""
import argparse
import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, ".")

from app.core.database import SyncSessionLocal
from app.models.market_data import KLine, SymbolInfo
from app.services.backtest import run_backtest
from app.services.data_provider import fetch_klines_from_sina, fetch_klines_from_eastmoney
from app.services.market import save_kline_data_sync
from app.models.strategy import StrategyType
from sqlalchemy import select, func

NAME_TO_SYMBOL = {
    "平安银行": "000001.SZ", "万科": "000002.SZ", "中国宝安": "000009.SZ",
    "中兴通讯": "000063.SZ", "中联重科": "000157.SZ", "潍柴动力": "000338.SZ",
    "金风科技": "002202.SZ", "立讯精密": "002475.SZ", "比亚迪": "002594.SZ",
    "宁德时代": "300750.SZ", "东方财富": "300059.SZ", "迈瑞医疗": "300760.SZ",
    "汇川技术": "300124.SZ", "阳光电源": "300274.SZ", "亿纬锂能": "300014.SZ",
    "浦发银行": "600000.SH", "中国石化": "600028.SH", "招商银行": "600036.SH",
    "中信证券": "600030.SH", "贵州茅台": "600519.SH", "海螺水泥": "600585.SH",
    "海尔智家": "600690.SH", "鲁抗医药": "600789.SH", "长江电力": "600900.SH",
    "中国神华": "601088.SH", "中国平安": "601318.SH", "工商银行": "601398.SH",
    "长城汽车": "601633.SH", "中国建筑": "601668.SH", "药明康德": "603259.SH",
    "新泉股份": "603179.SH", "科威尔": "688551.SH", "中芯国际": "688981.SH",
    "亨通光电": "600487.SH", "中兴通讯": "000063.SZ",
}


def resolve_symbol(name: str) -> str:
    """Look up symbol by name, fuzzy match."""
    # Direct symbol match
    if re.match(r"\d{6}\.(SH|SZ)", name, re.I):
        return name.upper()

    # Exact name match
    if name in NAME_TO_SYMBOL:
        return NAME_TO_SYMBOL[name]

    # Fuzzy search in DB
    db = SyncSessionLocal()
    try:
        results = db.execute(
            select(SymbolInfo).where(SymbolInfo.name.like(f"%{name}%"))
        ).scalars().all()
        if results:
            return results[0].symbol
    finally:
        db.close()

    # Fuzzy search in name map
    for n, s in NAME_TO_SYMBOL.items():
        if name in n or n in name:
            return s

    print(f"未找到'{name}'，请用代码格式如 002594.SZ")
    sys.exit(1)


def ensure_symbol(db, symbol: str, name: str = "?") -> SymbolInfo:
    """Ensure symbol exists in symbol_info, create if not."""
    sym = db.execute(
        select(SymbolInfo).where(SymbolInfo.symbol == symbol)
    ).scalars().first()
    if not sym:
        exchange = "SH" if ".SH" in symbol else "SZ"
        sym = SymbolInfo(
            symbol=symbol,
            name=name,
            exchange=exchange,
            asset_type="stock",
            status="active",
        )
        db.add(sym)
        db.commit()
        db.refresh(sym)
        print(f"  已添加标的: {symbol} {name}")
    return sym


def fetch_klines(symbol: str) -> int:
    """Fetch kline data from Sina, return number of new bars."""
    db = SyncSessionLocal()
    try:
        existing = db.execute(
            select(func.max(KLine.timestamp))
            .where(KLine.symbol == symbol, KLine.interval == "1d")
        ).scalar()

        end = datetime.now().strftime("%Y%m%d")
        if existing:
            start = (existing - timedelta(days=10)).strftime("%Y%m%d")
        else:
            start = (datetime.now() - timedelta(days=365 * 8)).strftime("%Y%m%d")

        print(f"  拉取 {symbol} K线 ({start} → {end})...", end=" ", flush=True)
        data = fetch_klines_from_sina(symbol, "1d", start, end)
        if not data:
            print("Sina 失败, 尝试东方财富...", end=" ", flush=True)
            time.sleep(1)
            data = fetch_klines_from_eastmoney(symbol, "1d", start, end)

        if data:
            inserted = save_kline_data_sync(db, symbol, "1d", data)
            db.commit()
            print(f"OK ({len(data)} 条, 新增 {inserted})")
            return inserted
        else:
            print("FAILED")
            return 0
    finally:
        db.close()


def load_klines(symbol: str) -> list:
    """Load kline data from DB."""
    db = SyncSessionLocal()
    try:
        rows = db.execute(
            select(KLine)
            .where(KLine.symbol == symbol, KLine.interval == "1d")
            .order_by(KLine.timestamp.asc())
        ).scalars().all()
        return [
            {
                "timestamp": int(r.timestamp.timestamp()),
                "open": r.open, "high": r.high, "low": r.low,
                "close": r.close, "volume": r.volume,
            }
            for r in rows
        ]
    finally:
        db.close()


def compute_indicators(klines: list) -> dict:
    """Basic technical snapshot."""
    import numpy as np
    closes = np.array([k["close"] for k in klines])
    highs = np.array([k["high"] for k in klines])
    lows = np.array([k["low"] for k in klines])
    volumes = np.array([k["volume"] for k in klines])
    n = len(closes)

    def ema(arr, p):
        out = np.zeros_like(arr)
        out[0] = arr[0]
        m = 2 / (p + 1)
        for i in range(1, len(arr)):
            out[i] = (arr[i] - out[i-1]) * m + out[i-1]
        return out

    def wilder_ema(arr, period):
        """Welles Wilder smoothing: alpha = 1/period (used for RSI, ADX)."""
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

    current_price = closes[-1]

    # RSI (14) — Wilder's smoothing per the original Welles Wilder formulation
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
    ma_trend = "bullish" if ma5 > ma20 > ma60 else "bearish" if ma5 < ma20 < ma60 else "mixed"

    change_5d = (closes[-1] / closes[-6] - 1) * 100 if n >= 6 else 0
    vol_ratio = np.mean(volumes[-5:]) / np.mean(volumes[-20:]) if n >= 20 else 1

    bb_std = np.std(closes[-20:])
    bb_mid = ma20
    bb_lower = bb_mid - 2 * bb_std
    bb_upper = bb_mid + 2 * bb_std
    bb_pos = (current_price - bb_lower) / (bb_upper - bb_lower) * 100 if bb_upper > bb_lower else 50

    ema12 = ema(closes, 12)
    ema26 = ema(closes, 26)
    macd_line = ema12 - ema26
    signal = ema(macd_line, 9)
    macd_hist = macd_line[-1] - signal[-1]

    macd_div_bull = False
    if n >= 40:
        hist_min = np.min(macd_line[-20:] - signal[-20:])
        if closes[-1] < closes[-20] and macd_hist > hist_min:
            macd_div_bull = True

    low_n = np.min(lows[-9:])
    high_n = np.max(highs[-9:])
    rsv = (current_price - low_n) / (high_n - low_n) * 100 if high_n > low_n else 50
    k_val = rsv * 1/3 + 50 * 2/3
    d_val = k_val * 1/3 + 50 * 2/3
    j_val = 3 * k_val - 2 * d_val

    cmf = 0
    if n >= 20:
        mf = ((closes[-20:] - lows[-20:]) - (highs[-20:] - closes[-20:])) / (highs[-20:] - lows[-20:] + 1e-9)
        cmf = np.sum(mf * volumes[-20:]) / np.sum(volumes[-20:])

    golden_cross = False
    death_cross = False
    if n >= 21:
        ma5_prev = np.mean(closes[-7:-2])
        ma20_prev = np.mean(closes[-22:-2])
        golden_cross = ma5_prev <= ma20_prev and ma5 > ma20
        death_cross = ma5_prev >= ma20_prev and ma5 < ma20

    return {
        "current_price": round(current_price, 2),
        "rsi": round(rsi, 1),
        "ma5": round(ma5, 2), "ma20": round(ma20, 2),
        "ma_trend": ma_trend,
        "change_5d": round(change_5d, 2),
        "vol_ratio": round(vol_ratio, 2),
        "bb_pos": round(bb_pos, 1),
        "macd_hist": round(macd_hist, 4),
        "macd_divergence_bull": macd_div_bull,
        "kdj_k": round(k_val, 1), "kdj_j": round(j_val, 1),
        "cmf": round(cmf, 4),
        "golden_cross": golden_cross,
        "death_cross": death_cross,
    }


def run_all_backtests(klines: list) -> dict:
    strategies = [
        ("MA交叉", StrategyType.MA_CROSS, {"fast_ma": 5, "slow_ma": 20}),
        ("MACD", StrategyType.MACD, {"fast": 12, "slow": 26, "signal": 9}),
        ("KDJ", StrategyType.KDJ, {"n": 9, "k": 3, "d": 3}),
        ("布林带", StrategyType.BOLLINGER, {"period": 20, "std": 2.0}),
        ("网格", StrategyType.GRID, {"grid_count": 5, "grid_spread": 0.03}),
    ]
    results = {}
    for name, stype, params in strategies:
        r = run_backtest(stype, params, klines)
        results[name] = {
            "total_return": round(r["total_return"] * 100, 1),
            "annual_return": round(r["annual_return"] * 100, 1),
            "max_drawdown": round(r["max_drawdown"] * 100, 1),
            "sharpe": round(r["sharpe_ratio"], 2),
            "win_rate": round(r["win_rate"] * 100, 1),
            "trades": r["total_trades"],
            "trade_list": r.get("trades", []),
        }
    return results


def classify_style(bt: dict) -> tuple:
    """Classify stock style based on backtest results."""
    trend_return = max(bt["MA交叉"]["annual_return"], bt["MACD"]["annual_return"])
    reversal_return = max(bt["KDJ"]["annual_return"], bt["布林带"]["annual_return"])
    if trend_return > reversal_return + 5:
        return "趋势型", "顺势策略（MA/MACD）有效，适合追涨杀跌"
    elif reversal_return > trend_return + 5:
        return "震荡型", "反转策略（KDJ/布林带）有效，适合抄底逃顶"
    else:
        return "混合型", "趋势和反转策略均有空间"


# ---------------------------------------------------------------------------
# Output formatters
# ---------------------------------------------------------------------------
def fmt_ts(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%d')


def print_header(symbol: str, name: str, ind: dict, kline_count: int):
    print(f"\n{'='*80}")
    print(f"  {symbol} {name}  深度分析报告")
    print(f"{'='*80}")
    print(f"  数据: {kline_count} 条日线 | 现价: {ind['current_price']} | "
          f"RSI: {ind['rsi']} | J: {ind['kdj_j']} | CMF: {ind['cmf']}")
    trend_label = "多头" if ind["ma_trend"] == "bullish" else "空头" if ind["ma_trend"] == "bearish" else "震荡"
    print(f"  均线: {trend_label} | MA5={ind['ma5']} MA20={ind['ma20']} | "
          f"5日涨跌: {ind['change_5d']:+.1f}% | 量比: {ind['vol_ratio']:.2f}")
    signals = []
    if ind["golden_cross"]: signals.append("金叉")
    if ind["death_cross"]: signals.append("死叉")
    if ind["macd_divergence_bull"]: signals.append("MACD底背离")
    print(f"  信号: {', '.join(signals) if signals else '无明显信号'}")


def print_backtest_summary(bt: dict):
    style, desc = classify_style(bt)
    print(f"\n  【风格判定】{style} — {desc}")
    print(f"\n  {'策略':<10} {'总收益%':>8} {'年化%':>8} {'最大回撤%':>8} {'夏普':>6} {'胜率%':>7} {'交易':>5}")
    print(f"  {'-'*56}")
    best = None
    for name, r in bt.items():
        marker = " ★" if best is None or r["annual_return"] > best[1]["annual_return"] else ""
        print(f"  {name:<10} {r['total_return']:>+8.1f} {r['annual_return']:>+8.1f} "
              f"{r['max_drawdown']:>8.1f} {r['sharpe']:>6.2f} {r['win_rate']:>7.1f} {r['trades']:>5}{marker}")
        if not best or r["annual_return"] > best[1]["annual_return"]:
            best = (name, r)
    print(f"\n  最佳策略: {best[0]} (年化 {best[1]['annual_return']:+.1f}%)")


def print_recommendation(symbol: str, name: str, ind: dict, bt: dict):
    style, _ = classify_style(bt)
    current = ind["current_price"]

    # Pick strategy and entry/exit conditions
    if style == "震荡型":
        best_st = max(bt["KDJ"], bt["布林带"], key=lambda x: x["annual_return"])
        strategy = "KDJ" if bt["KDJ"]["annual_return"] >= bt["布林带"]["annual_return"] else "布林带"
        entry = f"J < 20 超卖区金叉" if strategy == "KDJ" else f"价格触及布林下轨 + RSI < 30"
        exit_cond = f"J > 80 超买区死叉" if strategy == "KDJ" else f"价格回归布林中轨"
    else:
        strategy = "MA交叉" if bt["MA交叉"]["annual_return"] >= bt["MACD"]["annual_return"] else "MACD"
        entry = f"MA5上穿MA20 + 放量确认" if strategy == "MA交叉" else f"MACD金叉 + DIF上穿DEA"
        exit_cond = f"MA5下穿MA20 或 回撤 > 15%" if strategy == "MA交叉" else f"MACD死叉"

    print(f"\n  【建仓建议】")
    print(f"  推荐策略: {strategy}")
    print(f"  当前价:   {current}")
    print(f"  入场条件:  {entry}")
    print(f"  出场条件:  {exit_cond}")

    if ind["rsi"] > 70 or ind["kdj_j"] > 80:
        print(f"  ⚠ 当前RSI={ind['rsi']} J={ind['kdj_j']} 偏超买，建议等待回调")
    elif ind["rsi"] < 30 or ind["kdj_j"] < 0:
        print(f"  ✅ 当前RSI={ind['rsi']} J={ind['kdj_j']} 超卖区域，关注反弹信号")
    else:
        print(f"  ➤ 当前处于中间区域，等待触发信号")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="单股深度分析")
    parser.add_argument("target", help="股票名称或代码")
    parser.add_argument("--no-fetch", action="store_true", help="跳过数据拉取")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    args = parser.parse_args()

    symbol = resolve_symbol(args.target)

    db = SyncSessionLocal()
    try:
        info = db.execute(
            select(SymbolInfo).where(SymbolInfo.symbol == symbol)
        ).scalars().first()
    finally:
        db.close()

    name = info.name if info else symbol

    # Step 1: Register symbol
    db = SyncSessionLocal()
    try:
        ensure_symbol(db, symbol, name)
    finally:
        db.close()

    # Step 2: Fetch klines
    if not args.no_fetch:
        fetch_klines(symbol)

    # Step 3: Load data
    klines = load_klines(symbol)
    if len(klines) < 60:
        print(f"数据不足: 仅 {len(klines)} 条日线，需要至少 60 条")
        sys.exit(1)

    # Step 4: Compute indicators
    ind = compute_indicators(klines)

    # Step 5: Run backtests
    bt = run_all_backtests(klines)

    if args.json:
        out = {
            "symbol": symbol, "name": name,
            "kline_count": len(klines),
            "indicators": ind,
            "backtest": {k: {kk: vv for kk, vv in v.items() if kk != "trade_list"} for k, v in bt.items()},
            "style": classify_style(bt)[0],
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print_header(symbol, name, ind, len(klines))
        print_backtest_summary(bt)
        print_recommendation(symbol, name, ind, bt)

        # Trade detail summary
        print(f"\n{'='*80}")
        print(f"  各策略交易明细摘要")
        print(f"{'='*80}")
        for sname, r in bt.items():
            trades = r.get("trade_list", [])
            if not trades:
                continue
            wins = sum(1 for t in trades if t["action"] == "sell" and t.get("pnl", 0) > 0)
            total_pnl = sum(t.get("pnl", 0) for t in trades if t["action"] == "sell")
            print(f"\n  [{sname}] {len(trades)}笔 | 胜{wins} | 累计盈亏 {total_pnl:+.2f}")
            # Show first 3 and last 3 trades
            show = trades[:3] + ([{"_gap": True}] if len(trades) > 6 else []) + trades[-3:]
            for t in show:
                if isinstance(t, dict) and t.get("_gap"):
                    print(f"         ... 省略 {len(trades)-6} 笔 ...")
                    continue
                pnl_str = f'{t.get("pnl", 0):+.2f}' if t["action"] == "sell" else ""
                print(f"  {fmt_ts(t['timestamp'])} {t['action']:>5} @{t['price']:.2f} "
                      f"x{t['quantity']:.1f} {pnl_str:>10}")
