#!/usr/bin/env python3
"""
10大精选股票回测分析 — 每只股票使用推荐的最佳策略
"""
import logging, json, sys, os
logging.disable(logging.CRITICAL)
os.environ.setdefault("DEBUG", "true")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import datetime, timezone
import numpy as np
from app.core.database import SyncSessionLocal
from app.models.market_data import KLine
from app.services.backtest import run_backtest
from app.models.strategy import StrategyType
from app.services.data_provider import fetch_real_klines
from sqlalchemy import select

# ============================================================
# 股票列表 & 最佳策略（来自选股排名）
# ============================================================
STOCKS = [
    ("002475.SZ", "立讯精密", 71.29, StrategyType.MA_CROSS, {"fast_period": 5, "slow_period": 20}, "MA交叉(5,20)"),
    ("600487.SH", "亨通光电", 77.27, StrategyType.GRID, {"grid_levels": 10}, "网格(10层)"),
    ("002384.SZ", "东山精密", 211.39, StrategyType.MA_CROSS, {"fast_period": 5, "slow_period": 20}, "MA交叉(5,20)"),
    ("300308.SZ", "中际旭创", 886.0, StrategyType.MA_CROSS, {"fast_period": 5, "slow_period": 20}, "MA交叉(5,20)"),
    ("002916.SZ", "深南电路", 320.37, StrategyType.GRID, {"grid_levels": 10}, "网格(10层)"),
    ("600183.SH", "生益科技", 82.0, StrategyType.KDJ, {"n": 9, "k": 3, "d": 3}, "KDJ(9,3,3)"),
    ("002463.SZ", "沪电股份", 108.71, StrategyType.KDJ, {"n": 9, "k": 3, "d": 3}, "KDJ(9,3,3)"),
    ("000977.SZ", "浪潮信息", 76.48, StrategyType.KDJ, {"n": 9, "k": 3, "d": 3}, "KDJ(9,3,3)"),
    ("688256.SH", "寒武纪", 1182.53, StrategyType.MA_CROSS, {"fast_period": 5, "slow_period": 20}, "MA交叉(5,20)"),
    ("688008.SH", "澜起科技", 210.27, StrategyType.KDJ, {"n": 9, "k": 3, "d": 3}, "KDJ(9,3,3)"),
]

# 全策略列表（用于对比）
ALL_STRATEGIES = [
    ("MA交叉(5,20)", StrategyType.MA_CROSS, {"fast_period": 5, "slow_period": 20}),
    ("MACD(12,26,9)", StrategyType.MACD, {"fast": 12, "slow": 26, "signal": 9}),
    ("KDJ(9,3,3)", StrategyType.KDJ, {"n": 9, "k": 3, "d": 3}),
    ("布林带(20,2)", StrategyType.BOLLINGER, {"period": 20, "std": 2.0}),
    ("网格(10层)", StrategyType.GRID, {"grid_levels": 10}),
]

def fetch_klines(symbol, interval="1d"):
    """从数据库获取K线数据"""
    db = SyncSessionLocal()
    rows = db.execute(
        select(KLine).where(KLine.symbol == symbol, KLine.interval == interval)
        .order_by(KLine.timestamp.asc())
    ).scalars().all()
    db.close()

    if rows:
        return [{
            "timestamp": int(r.timestamp.timestamp()),
            "open": r.open, "high": r.high, "low": r.low,
            "close": r.close, "volume": r.volume,
        } for r in rows]

    # 数据库没有则从网络获取
    data = fetch_real_klines(symbol, interval)
    if data:
        return [{
            "timestamp": int(d["timestamp"].timestamp()) if hasattr(d["timestamp"], "timestamp") else int(d["timestamp"]),
            "open": d["open"], "high": d["high"], "low": d["low"],
            "close": d["close"], "volume": d.get("volume", 0),
        } for d in data]
    return None

def print_sep(title=None):
    if title:
        print(f"\n{'='*78}")
        print(f"  {title}")
        print(f"{'='*78}")
    else:
        print(f"{'─'*78}")

# ============================================================
# Part 1: 五策略全面对比（所有股票 × 所有策略）
# ============================================================
print_sep("TOP 10 精选股票 — 全策略回测对比")
print(f"{'股票':<12} {'策略':<16} {'总收益%':>9} {'年化%':>9} {'最大回撤%':>9} {'夏普':>6} {'胜率%':>7} {'交易':>5} {'盈亏比':>7}")
print(f"{'─'*78}")

all_results = {}

for symbol, name, price, best_type, best_params, best_label in STOCKS:
    klines = fetch_klines(symbol)
    if not klines or len(klines) < 50:
        print(f"{symbol:<8} {name:<8}  ❌ 数据不足")
        continue

    closes = np.array([k['close'] for k in klines])
    start_date = datetime.fromtimestamp(klines[0]["timestamp"]).strftime("%Y-%m-%d")
    end_date = datetime.fromtimestamp(klines[-1]["timestamp"]).strftime("%Y-%m-%d")

    stock_results = {}
    for slabel, stype, sparams in ALL_STRATEGIES:
        # 网格策略自动计算价格区间
        if stype == StrategyType.GRID:
            c = [k['close'] for k in klines]
            mid = sum(c) / len(c)
            sparams = {"grid_levels": 10, "upper_price": round(mid * 1.5, 2), "lower_price": round(mid * 0.5, 2)}

        r = run_backtest(stype, sparams, klines, initial_capital=100000)
        stock_results[slabel] = r

        trades = [t for t in r.get('trades', []) if t['action'] == 'sell' and t.get('pnl') is not None]
        wins = sum(1 for t in trades if t['pnl'] > 0)
        gross_profit = sum(t['pnl'] for t in trades if t['pnl'] > 0)
        gross_loss = abs(sum(t['pnl'] for t in trades if t['pnl'] < 0))
        pf = gross_profit / gross_loss if gross_loss > 0 else float('inf')

        # 标记最佳策略
        marker = " ★" if slabel == best_label else "  "
        print(f"{symbol:<8} {name:<4} {slabel:<14}{marker} "
              f"{r['total_return']*100:>+8.1f}% {r['annual_return']*100:>+8.1f}% "
              f"{r['max_drawdown']*100:>7.1f}% {r['sharpe_ratio']:>6.2f} "
              f"{r['win_rate']*100:>5.1f}% {r['total_trades']:>5} {pf:>7.2f}")

    all_results[(symbol, name)] = stock_results
    print()

# ============================================================
# Part 2: 每只股票的深度分析（最佳策略）
# ============================================================
print_sep("深度分析 — 各股票最佳策略明细")

for symbol, name, price, best_type, best_params, best_label in STOCKS:
    klines = fetch_klines(symbol)
    if not klines or len(klines) < 50:
        continue

    closes = np.array([k['close'] for k in klines])
    start_date = datetime.fromtimestamp(klines[0]["timestamp"]).strftime("%Y-%m-%d")
    end_date = datetime.fromtimestamp(klines[-1]["timestamp"]).strftime("%Y-%m-%d")

    # 最佳策略参数调优（仅对MA交叉做参数优化）
    print_sep(f"{symbol} {name}  现价 {price:.2f}  数据 {len(klines)}条 ({start_date}~{end_date})")
    print(f"  推荐策略: {best_label}  |  RSI: 待查  |  趋势: 多头")

    # ============================================================
    # 2a. 最佳策略回测结果
    # ============================================================
    # 网格特殊处理
    actual_params = best_params.copy()
    if best_type == StrategyType.GRID:
        c = [k['close'] for k in klines]
        mid = sum(c) / len(c)
        actual_params = {"grid_levels": 10, "upper_price": round(mid * 1.5, 2), "lower_price": round(mid * 0.5, 2)}

    r = run_backtest(best_type, actual_params, klines, initial_capital=100000)
    trades = r.get('trades', [])
    sell_trades = [t for t in trades if t['action'] == 'sell' and t.get('pnl') is not None]
    wins = sum(1 for t in sell_trades if t['pnl'] > 0)
    gross_profit = sum(t['pnl'] for t in sell_trades if t['pnl'] > 0)
    gross_loss = abs(sum(t['pnl'] for t in sell_trades if t['pnl'] < 0))
    pf = gross_profit / gross_loss if gross_loss > 0 else float('inf')

    print(f"\n  [最佳策略回测] {best_label}")
    print(f"  {'─'*50}")
    print(f"    总收益率:   {r['total_return']*100:>+8.2f}%")
    print(f"    年化收益率: {r['annual_return']*100:>+8.2f}%")
    print(f"    最大回撤:   {r['max_drawdown']*100:>8.2f}%")
    print(f"    夏普比率:   {r['sharpe_ratio']:>8.4f}")
    print(f"    最终权益:   {r['final_equity']:>10.2f}")
    print(f"    总交易:     {r['total_trades']:>4}笔")
    print(f"    盈利交易:   {r['profit_trades']:>4}笔 (胜率 {r['win_rate']*100:.1f}%)")
    print(f"    亏损交易:   {r['loss_trades']:>4}笔")
    print(f"    盈亏比:     {pf:>8.2f}")

    # ============================================================
    # 2b. 所有策略横向对比（简化版）
    # ============================================================
    stock_res = all_results.get((symbol, name), {})
    if stock_res:
        print(f"\n  五策略排名（按年化收益）:")
        ranked = sorted(stock_res.items(), key=lambda x: x[1]['annual_return'], reverse=True)
        print(f"  {'排名':>4} {'策略':<16} {'总收益%':>9} {'年化%':>9} {'最大回撤%':>9} {'夏普':>6} {'胜率%':>7}")
        print(f"  {'─'*60}")
        for rank, (sname, sr) in enumerate(ranked, 1):
            marker = " ◀ 推荐" if sname == best_label else ""
            print(f"  {rank:>3}. {sname:<14} {sr['total_return']*100:>+8.1f}% {sr['annual_return']*100:>+8.1f}% "
                  f"{sr['max_drawdown']*100:>7.1f}% {sr['sharpe_ratio']:>6.2f} {sr['win_rate']*100:>5.1f}%{marker}")

    # ============================================================
    # 2c. MA交叉参数优化（仅限MA交叉策略的股票）
    # ============================================================
    if best_type == StrategyType.MA_CROSS:
        print(f"\n  MA参数优化:")
        print(f"  {'快线':>4} {'慢线':>4} {'总收益%':>9} {'年化%':>9} {'夏普':>6} {'回撤%':>7} {'胜率%':>6} {'交易':>4}")
        print(f"  {'─'*52}")
        best_ar = -999
        best_fast, best_slow = 5, 20
        for fast in [3, 5, 10, 15, 20]:
            for slow in [10, 15, 20, 30, 45, 60]:
                if fast >= slow: continue
                mr = run_backtest(StrategyType.MA_CROSS, {'fast_period': fast, 'slow_period': slow}, klines)
                ar = mr['annual_return'] * 100
                print(f"  {fast:>4} {slow:>4} {mr['total_return']*100:>+8.1f}% {ar:>+8.1f}% "
                      f"{mr['sharpe_ratio']:>6.2f} {mr['max_drawdown']*100:>6.1f}% {mr['win_rate']*100:>5.1f}% {mr['total_trades']:>4}")
                if ar > best_ar:
                    best_ar = ar
                    best_fast, best_slow = fast, slow

        print(f"\n  ⭐ 最优参数: MA{best_fast}/{best_slow}  年化 {best_ar:+.1f}%")

    # ============================================================
    # 2d. KDJ参数优化（仅限KDJ策略的股票）
    # ============================================================
    if best_type == StrategyType.KDJ:
        print(f"\n  KDJ参数优化:")
        print(f"  {'N':>4} {'K':>4} {'D':>4} {'总收益%':>9} {'年化%':>9} {'夏普':>6} {'回撤%':>7} {'胜率%':>6} {'交易':>4}")
        print(f"  {'─'*62}")
        best_ar = -999
        best_n, best_k, best_d = 9, 3, 3
        for n in [5, 9, 14, 20]:
            for kk in [2, 3, 5]:
                for dd in [2, 3, 5]:
                    if kk == dd: continue
                    mr = run_backtest(StrategyType.KDJ, {'n': n, 'k': kk, 'd': dd}, klines)
                    ar = mr['annual_return'] * 100
                    print(f"  {n:>4} {kk:>4} {dd:>4} {mr['total_return']*100:>+8.1f}% {ar:>+8.1f}% "
                          f"{mr['sharpe_ratio']:>6.2f} {mr['max_drawdown']*100:>6.1f}% {mr['win_rate']*100:>5.1f}% {mr['total_trades']:>4}")
                    if ar > best_ar:
                        best_ar = ar
                        best_n, best_k, best_d = n, kk, dd

        print(f"\n  ⭐ 最优参数: KDJ({best_n},{best_k},{best_d})  年化 {best_ar:+.1f}%")

    # ============================================================
    # 2e. 最近交易明细
    # ============================================================
    print(f"\n  最近20笔交易明细:")
    recent = trades[-20:] if len(trades) > 20 else trades
    for t in recent:
        ts = datetime.fromtimestamp(t['timestamp']).strftime('%Y-%m-%d')
        action = t['action']
        if action == 'buy':
            print(f"    {ts}  买入  @{t['price']:.2f}  x{t['quantity']:.0f}")
        else:
            pnl = t.get('pnl', 0)
            print(f"    {ts}  卖出  @{t['price']:.2f}  x{t['quantity']:.0f}  "
                  f"{'盈利' if pnl>0 else '亏损'}: {pnl:+.2f}")

    # ============================================================
    # 2f. 近一年回测
    # ============================================================
    print(f"\n  近一年回测 ({best_label}):")
    one_year_ago = int(datetime.now().timestamp()) - 365 * 86400
    recent_klines = [k for k in klines if k['timestamp'] >= one_year_ago]
    if len(recent_klines) < 60:
        recent_klines = klines[-252:]
    print(f"    数据: {len(recent_klines)} 条K线")

    r1 = run_backtest(best_type, actual_params, recent_klines, initial_capital=100000)
    sell_1y = [t for t in r1.get('trades', []) if t['action'] == 'sell' and t.get('pnl') is not None]
    wins_1y = sum(1 for t in sell_1y if t['pnl'] > 0)
    print(f"    总收益: {r1['total_return']*100:+.1f}%  年化: {r1['annual_return']*100:+.1f}%")
    print(f"    夏普: {r1['sharpe_ratio']:.2f}  最大回撤: {r1['max_drawdown']*100:.1f}%")
    print(f"    交易: {r1['total_trades']}笔  胜率: {r1['win_rate']*100:.0f}%")

    for t in r1.get('trades', []):
        ts = datetime.fromtimestamp(t['timestamp']).strftime('%Y-%m-%d')
        if t['action'] == 'buy':
            print(f"      {ts}  买入  @{t['price']:.2f}")
        else:
            print(f"      {ts}  卖出  @{t['price']:.2f}  盈亏: {t.get('pnl',0):+.0f}")

    print()

# ============================================================
# Part 3: 综合排名
# ============================================================
print_sep("综合排名（按最佳策略年化收益）")

# 按最佳策略排名
rankings = []
for symbol, name, price, best_type, best_params, best_label in STOCKS:
    klines = fetch_klines(symbol)
    if not klines or len(klines) < 50:
        continue

    actual_params = best_params.copy()
    if best_type == StrategyType.GRID:
        c = [k['close'] for k in klines]
        mid = sum(c) / len(c)
        actual_params = {"grid_levels": 10, "upper_price": round(mid * 1.5, 2), "lower_price": round(mid * 0.5, 2)}

    r = run_backtest(best_type, actual_params, klines, initial_capital=100000)
    trades = [t for t in r.get('trades', []) if t['action'] == 'sell' and t.get('pnl') is not None]
    wins = sum(1 for t in trades if t['pnl'] > 0)
    gross_profit = sum(t['pnl'] for t in trades if t['pnl'] > 0)
    gross_loss = abs(sum(t['pnl'] for t in trades if t['pnl'] < 0))
    pf = gross_profit / gross_loss if gross_loss > 0 else float('inf')

    rankings.append((r['annual_return'], symbol, name, price, best_label, r))

rankings.sort(key=lambda x: x[0], reverse=True)

print(f"{'排名':>4} {'代码':<12} {'名称':<8} {'现价':>8} {'策略':<14} {'年化%':>8} {'总收益%':>9} {'最大回撤%':>8} {'夏普':>6} {'胜率%':>6}")
print(f"{'─'*78}")
for i, (ar, symbol, name, price, bl, r) in enumerate(rankings, 1):
    print(f"{i:>4}  {symbol:<10} {name:<6} {price:>8.2f} {bl:<12} "
          f"{ar*100:>+7.1f}% {r['total_return']*100:>+8.1f}% {r['max_drawdown']*100:>7.1f}% "
          f"{r['sharpe_ratio']:>6.2f} {r['win_rate']*100:>5.1f}%")

print(f"\n{'='*78}")
print(f"  报告生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print(f"{'='*78}")
