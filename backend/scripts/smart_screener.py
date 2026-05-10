#!/usr/bin/env python3
"""
妙想智能选股引擎 — 条件筛选 / 回测排行 / 相似股发现 / 批量扫描
"""
import argparse
import json
import sys
from datetime import datetime, timezone

sys.path.insert(0, ".")

from app.core.database import SyncSessionLocal
from app.models.market_data import KLine, SymbolInfo
from app.services.backtest import run_backtest
from app.models.strategy import StrategyType
from sqlalchemy import select

import numpy as np


def load_all_klines():
    """加载所有活跃标的的日线数据。"""
    db = SyncSessionLocal()
    try:
        symbols = db.execute(
            select(SymbolInfo).where(SymbolInfo.status == "active")
        ).scalars().all()

        all_data = {}
        for sym in symbols:
            rows = db.execute(
                select(KLine)
                .where(KLine.symbol == sym.symbol, KLine.interval == "1d")
                .order_by(KLine.timestamp.asc())
            ).scalars().all()
            if len(rows) >= 60:
                all_data[sym.symbol] = {
                    "name": sym.name,
                    "rows": rows,
                    "klines": [
                        {
                            "timestamp": int(r.timestamp.timestamp()),
                            "open": r.open,
                            "high": r.high,
                            "low": r.low,
                            "close": r.close,
                            "volume": r.volume,
                        }
                        for r in rows
                    ],
                }
        return all_data
    finally:
        db.close()


def compute_indicators(data):
    """Compute basic technical indicators for screening."""
    closes = np.array([k["close"] for k in data["klines"]])
    highs = np.array([k["high"] for k in data["klines"]])
    lows = np.array([k["low"] for k in data["klines"]])
    volumes = np.array([k["volume"] for k in data["klines"]])

    n = len(closes)
    if n < 60:
        return None

    current_price = closes[-1]

    # RSI (14) — Wilder's smoothing per the original Welles Wilder formulation
    delta_all = np.diff(closes, prepend=closes[0])
    gain_arr = np.where(delta_all > 0, delta_all, 0)
    loss_arr = np.where(delta_all < 0, -delta_all, 0)
    avg_gain = _wilder_ema(gain_arr, 14)[-1]
    avg_loss = _wilder_ema(loss_arr, 14)[-1]
    if not np.isnan(avg_gain) and not np.isnan(avg_loss) and avg_loss > 0:
        rsi = 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))
    else:
        rsi = 100.0 if avg_loss == 0 else 50.0

    # MA
    ma5 = np.mean(closes[-5:])
    ma20 = np.mean(closes[-20:])
    ma60 = np.mean(closes[-60:]) if n >= 60 else ma20
    ma_trend = "bullish" if ma5 > ma20 > ma60 else "bearish" if ma5 < ma20 < ma60 else "mixed"

    # Price change
    change_5d = (closes[-1] / closes[-6] - 1) * 100 if n >= 6 else 0

    # Volume ratio
    vol_ratio = np.mean(volumes[-5:]) / np.mean(volumes[-20:]) if n >= 20 else 1

    # BB position
    bb_std = np.std(closes[-20:])
    bb_mid = ma20
    bb_lower = bb_mid - 2 * bb_std
    bb_upper = bb_mid + 2 * bb_std
    bb_pos = (current_price - bb_lower) / (bb_upper - bb_lower) * 100 if bb_upper > bb_lower else 50

    # MACD
    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)
    macd_line = ema12 - ema26
    signal = _ema(macd_line, 9)
    macd_hist = macd_line[-1] - signal[-1]
    macd_divergence_bull = False
    if n >= 40 and closes[-1] < closes[-20] and macd_hist > np.min([macd_line[i] - signal[i] for i in range(-20, -1)]):
        macd_divergence_bull = True

    # KDJ
    low_n = np.min(lows[-9:])
    high_n = np.max(highs[-9:])
    rsv = (current_price - low_n) / (high_n - low_n) * 100 if high_n > low_n else 50
    k = rsv * 1/3 + 50 * 2/3  # simplified
    d = k * 1/3 + 50 * 2/3
    j = 3 * k - 2 * d

    # CMF estimate
    cmf = 0
    if n >= 20:
        mf_multipliers = ((closes[-20:] - lows[-20:]) - (highs[-20:] - closes[-20:])) / (
            highs[-20:] - lows[-20:] + 1e-9
        )
        mf_volume = mf_multipliers * volumes[-20:]
        cmf = np.sum(mf_volume) / np.sum(volumes[-20:])

    # Golden cross
    golden_cross = False
    if n >= 21:
        ma5_prev = np.mean(closes[-7:-2])
        ma20_prev = np.mean(closes[-22:-2])
        golden_cross = ma5_prev <= ma20_prev and ma5 > ma20

    death_cross = False
    if n >= 21:
        death_cross = ma5_prev >= ma20_prev and ma5 < ma20

    return {
        "current_price": round(current_price, 2),
        "rsi": round(rsi, 1),
        "ma5": round(ma5, 2),
        "ma20": round(ma20, 2),
        "ma_trend": ma_trend,
        "change_5d": round(change_5d, 2),
        "vol_ratio": round(vol_ratio, 2),
        "bb_pos": round(bb_pos, 1),
        "macd_hist": round(macd_hist, 4),
        "macd_divergence_bull": macd_divergence_bull,
        "kdj_j": round(j, 1),
        "kdj_k": round(k, 1),
        "cmf": round(cmf, 4),
        "golden_cross": golden_cross,
        "death_cross": death_cross,
    }


def _ema(arr, period):
    result = np.zeros_like(arr)
    result[0] = arr[0]
    multiplier = 2 / (period + 1)
    for i in range(1, len(arr)):
        result[i] = (arr[i] - result[i - 1]) * multiplier + result[i - 1]
    return result


def _wilder_ema(arr, period):
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


def _rolling_mean(arr, period):
    return np.convolve(arr, np.ones(period) / period, mode="valid")


def run_backtests(kline_data):
    """Run all 5 strategies and return results dict."""
    strategies = [
        ("MA交叉", StrategyType.MA_CROSS, {"fast_ma": 5, "slow_ma": 20}),
        ("MACD", StrategyType.MACD, {"fast": 12, "slow": 26, "signal": 9}),
        ("KDJ", StrategyType.KDJ, {"n": 9, "k": 3, "d": 3}),
        ("布林带", StrategyType.BOLLINGER, {"period": 20, "std": 2.0}),
        ("网格", StrategyType.GRID, {"grid_count": 5, "grid_spread": 0.03}),
    ]
    results = {}
    for name, stype, params in strategies:
        r = run_backtest(stype, params, kline_data)
        results[name] = {
            "total_return": round(r["total_return"] * 100, 1),
            "annual_return": round(r["annual_return"] * 100, 1),
            "max_drawdown": round(r["max_drawdown"] * 100, 1),
            "sharpe": round(r["sharpe_ratio"], 2),
            "win_rate": round(r["win_rate"] * 100, 1),
            "trades": r["total_trades"],
        }
    return results


def filter_stocks(all_data, conditions):
    """Apply condition filters and return matching stocks with scores."""
    results = []
    for symbol, data in all_data.items():
        ind = compute_indicators(data)
        if ind is None:
            continue

        bt = run_backtests(data["klines"])

        match = True
        reasons = []

        for cond in conditions:
            if cond == "超卖" and not (ind["rsi"] < 30 or ind["kdj_j"] < 20):
                match = False
            if cond == "超买" and not (ind["rsi"] > 70 or ind["kdj_j"] > 80):
                match = False
            if cond == "破净":
                # PB not available in this quick scan, skip
                pass
            if cond == "低估值":
                # PE not available in quick scan
                pass
            if cond == "资金流入" and ind["cmf"] < 0.1:
                match = False
            if cond == "底背离" and not ind["macd_divergence_bull"]:
                match = False
            if cond == "多头排列" and ind["ma_trend"] != "bullish":
                match = False
            if cond == "趋势":
                trend_score = max(bt["MA交叉"]["total_return"], bt["MACD"]["total_return"])
                if trend_score < 10:
                    match = False
            if cond == "震荡":
                reversal_score = max(bt["KDJ"]["total_return"], bt["布林带"]["total_return"])
                if reversal_score < 10:
                    match = False
            if cond == "金叉" and not ind["golden_cross"]:
                match = False
            if cond == "死叉" and not ind["death_cross"]:
                match = False

        if match:
            best_strategy = max(bt.items(), key=lambda x: x[1]["annual_return"])
            results.append({
                "symbol": symbol,
                "name": data["name"],
                "price": ind["current_price"],
                "rsi": ind["rsi"],
                "kdj_j": ind["kdj_j"],
                "cmf": ind["cmf"],
                "ma_trend": ind["ma_trend"],
                "change_5d": ind["change_5d"],
                "macd_div_bull": ind["macd_divergence_bull"],
                "golden_cross": ind["golden_cross"],
                "best_strategy": best_strategy[0],
                "best_annual": best_strategy[1]["annual_return"],
                "backtest": bt,
                "indicators": ind,
            })

    return results


def find_similar(all_data, target_symbol, top_n=5):
    """Find stocks with similar backtest profiles."""
    if target_symbol not in all_data:
        return []

    target_bt = run_backtests(all_data[target_symbol]["klines"])
    target_vec = np.array([target_bt[s]["annual_return"] for s in ["MA交叉", "MACD", "KDJ", "布林带", "网格"]])

    similarities = []
    for symbol, data in all_data.items():
        if symbol == target_symbol:
            continue
        bt = run_backtests(data["klines"])
        vec = np.array([bt[s]["annual_return"] for s in ["MA交叉", "MACD", "KDJ", "布林带", "网格"]])
        # Cosine similarity
        dot = np.dot(target_vec, vec)
        norm_t = np.linalg.norm(target_vec)
        norm_v = np.linalg.norm(vec)
        sim = dot / (norm_t * norm_v) if norm_t * norm_v > 0 else 0
        similarities.append({
            "symbol": symbol,
            "name": data["name"],
            "similarity": round(sim, 4),
            "backtest": bt,
        })

    similarities.sort(key=lambda x: x["similarity"], reverse=True)
    return similarities[:top_n]


def rank_by_strategy(all_data, strategy_name, top_n=10):
    """Rank all stocks by a specific strategy's annual return."""
    results = []
    for symbol, data in all_data.items():
        bt = run_backtests(data["klines"])
        if strategy_name in bt:
            results.append({
                "symbol": symbol,
                "name": data["name"],
                "annual_return": bt[strategy_name]["annual_return"],
                "total_return": bt[strategy_name]["total_return"],
                "sharpe": bt[strategy_name]["sharpe"],
                "max_drawdown": bt[strategy_name]["max_drawdown"],
                "backtest": bt,
            })
    results.sort(key=lambda x: x["annual_return"], reverse=True)
    return results[:top_n]


def print_results_table(results, title="选股结果"):
    """Print formatted table."""
    print(f"\n{'='*90}")
    print(f"  {title}")
    print(f"{'='*90}")
    print(f"{'排名':<4} {'代码':<12} {'名称':<8} {'现价':<6} {'RSI':<5} {'5日%':<7} {'趋势':<6} {'最佳策略':<8} {'年化%':<7}")
    print("-" * 90)

    for i, r in enumerate(results):
        trend = "多头" if r.get("ma_trend") == "bullish" else "空头" if r.get("ma_trend") == "bearish" else "震荡"
        print(
            f"{i+1:<4} {r['symbol']:<12} {r.get('name','?'):<8} {r.get('price','?'):<6} "
            f"{r.get('rsi','?'):<5} {r.get('change_5d','?'):<7} {trend:<6} "
            f"{r.get('best_strategy','?'):<8} {r.get('best_annual','?'):<7}"
        )


def print_backtest_table(results, title="回测排行"):
    """Print backtest ranking table."""
    print(f"\n{'='*100}")
    print(f"  {title}")
    print(f"{'='*100}")
    print(f"{'排名':<4} {'代码':<12} {'名称':<10} {'总收益%':<8} {'年化%':<7} {'夏普':<6} {'最大回撤%':<9}")
    print("-" * 100)

    for i, r in enumerate(results):
        print(
            f"{i+1:<4} {r['symbol']:<12} {r.get('name','?'):<10} "
            f"{r.get('total_return','?'):<8} {r.get('annual_return','?'):<7} "
            f"{r.get('sharpe','?'):<6} {r.get('max_drawdown','?'):<9}"
        )


def cmd_filter(conditions, top):
    all_data = load_all_klines()
    print(f"已加载 {len(all_data)} 只标的")
    results = filter_stocks(all_data, conditions)
    results.sort(key=lambda x: x["best_annual"], reverse=True)
    top_results = results[:top]
    print_results_table(top_results, f"选股条件: {', '.join(conditions)}")
    print_backtest_table(top_results, f"Top {top} 最佳策略回测")


def cmd_similar(symbol, top):
    all_data = load_all_klines()
    target = all_data.get(symbol)
    if not target:
        # Try with suffix
        for s, d in all_data.items():
            if s.startswith(symbol):
                symbol = s
                target = d
                break
    if not target:
        print(f"未找到标的: {symbol}")
        return

    results = find_similar(all_data, symbol, top)
    print(f"\n与 {symbol} {target['name']} 最相似的 {len(results)} 只标的：")
    for r in results:
        print(f"\n{r['symbol']} {r['name']} (相似度: {r['similarity']:.2%})")
        print(f"  MA交叉: {r['backtest']['MA交叉']['annual_return']}%   MACD: {r['backtest']['MACD']['annual_return']}%")
        print(f"  KDJ: {r['backtest']['KDJ']['annual_return']}%   布林带: {r['backtest']['布林带']['annual_return']}%")
        print(f"  网格: {r['backtest']['网格']['annual_return']}%")


def cmd_rank(strategy, top):
    all_data = load_all_klines()
    print(f"已加载 {len(all_data)} 只标的")
    results = rank_by_strategy(all_data, strategy, top)
    print_backtest_table(results, f"{strategy} 策略排行 Top {top}")


def cmd_scan(top):
    all_data = load_all_klines()
    print(f"已加载 {len(all_data)} 只标的，正在扫描...\n")

    results = []
    for symbol, data in all_data.items():
        ind = compute_indicators(data)
        if ind is None:
            continue
        bt = run_backtests(data["klines"])
        best = max(bt.items(), key=lambda x: x[1]["annual_return"])
        # Composite quick score (simplified)
        score = 50
        if ind["ma_trend"] == "bullish":
            score += 10
        if ind["rsi"] < 30:
            score += 8
        if ind["macd_divergence_bull"]:
            score += 5
        if ind["golden_cross"]:
            score += 8
        if ind["cmf"] > 0.1:
            score += 5
        if ind["ma_trend"] == "bearish":
            score -= 8
        if ind["cmf"] < -0.1:
            score -= 5

        results.append({
            "symbol": symbol,
            "name": data["name"],
            "price": ind["current_price"],
            "rsi": ind["rsi"],
            "score": score,
            "change_5d": ind["change_5d"],
            "ma_trend": ind["ma_trend"],
            "best_strategy": best[0],
            "best_annual": best[1]["annual_return"],
            "backtest": bt,
            "indicators": ind,
        })

    results.sort(key=lambda x: x["score"], reverse=True)
    top_results = results[:top]

    print(f"{'排名':<4} {'代码':<12} {'名称':<8} {'现价':<6} {'RSI':<5} {'评分':<5} {'趋势':<6} {'最佳策略':<8} {'年化%':<7}")
    print("-" * 90)
    for i, r in enumerate(top_results):
        trend = "多头" if r["ma_trend"] == "bullish" else "空头" if r["ma_trend"] == "bearish" else "震荡"
        print(
            f"{i+1:<4} {r['symbol']:<12} {r['name']:<8} {r['price']:<6} "
            f"{r['rsi']:<5} {r['score']:<5} {trend:<6} "
            f"{r['best_strategy']:<8} {r['best_annual']:<7}"
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="妙想智能选股引擎")
    parser.add_argument("--filter", type=str, help="条件选股，逗号分隔")
    parser.add_argument("--similar", type=str, help="相似股发现，输入标的代码")
    parser.add_argument("--rank", type=str, help="回测排行，输入策略名称")
    parser.add_argument("--scan", action="store_true", help="批量扫描")
    parser.add_argument("--top", type=int, default=10, help="输出Top N")
    args = parser.parse_args()

    if args.filter:
        conditions = [c.strip() for c in args.filter.split(",")]
        cmd_filter(conditions, args.top)
    elif args.similar:
        cmd_similar(args.similar, args.top)
    elif args.rank:
        cmd_rank(args.rank, args.top)
    elif args.scan:
        cmd_scan(args.top)
    else:
        parser.print_help()
