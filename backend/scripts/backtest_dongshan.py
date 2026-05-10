#!/usr/bin/env python3
"""东山精密 002384.SZ 详尽回测报告"""
import logging, json, sys
logging.disable(logging.CRITICAL)
sys.path.insert(0, '.')
from datetime import datetime, timezone
import numpy as np
from app.core.database import SyncSessionLocal
from app.models.market_data import KLine
from app.services.backtest import run_backtest
from app.models.strategy import StrategyType
from sqlalchemy import select

db = SyncSessionLocal()
rows = db.execute(
    select(KLine).where(KLine.symbol == '002384.SZ', KLine.interval == '1d')
    .order_by(KLine.timestamp.asc())
).scalars().all()
db.close()

klines = [{'timestamp': int(r.timestamp.timestamp()), 'open': r.open, 'high': r.high,
           'low': r.low, 'close': r.close, 'volume': r.volume} for r in rows]
closes = np.array([k['close'] for k in klines])

print(f'{"="*70}')
print(f'  东山精密 002384.SZ  详尽回测报告')
print(f'  数据: {len(klines)} 条日线')
print(f'  区间: {datetime.fromtimestamp(klines[0]["timestamp"]).strftime("%Y-%m-%d")} ~ '
      f'{datetime.fromtimestamp(klines[-1]["timestamp"]).strftime("%Y-%m-%d")}')
print(f'  最新价: {closes[-1]:.2f}')
print(f'{"="*70}')

# ============================================================
# 1. 五策略回测对比（全量数据）
# ============================================================
strategies = [
    ('MA交叉(5,20)', StrategyType.MA_CROSS, {'fast_ma': 5, 'slow_ma': 20}),
    ('MACD(12,26,9)', StrategyType.MACD, {'fast': 12, 'slow': 26, 'signal': 9}),
    ('KDJ(9,3,3)', StrategyType.KDJ, {'n': 9, 'k': 3, 'd': 3}),
    ('布林带(20,2)', StrategyType.BOLLINGER, {'period': 20, 'std': 2.0}),
    ('网格(5格)', StrategyType.GRID, {'grid_count': 5, 'grid_spread': 0.03}),
]

print(f'\n一、五策略回测对比')
print(f'{"策略":<16} {"总收益%":>9} {"年化%":>9} {"最大回撤%":>10} {"夏普":>6} {"胜率%":>7} {"交易":>6} {"盈亏比":>8}')
print(f'{"-"*71}')
for sname, stype, params in strategies:
    r = run_backtest(stype, params, klines, initial_capital=10000)
    trades = [t for t in r.get('trades', []) if t['action'] == 'sell' and t.get('pnl') is not None]
    wins = sum(1 for t in trades if t['pnl'] > 0)
    gross_profit = sum(t['pnl'] for t in trades if t['pnl'] > 0)
    gross_loss = abs(sum(t['pnl'] for t in trades if t['pnl'] < 0))
    pf = gross_profit / gross_loss if gross_loss > 0 else float('inf')
    print(f'{sname:<16} {r["total_return"]*100:>+8.1f}% {r["annual_return"]*100:>+8.1f}% '
          f'{r["max_drawdown"]*100:>8.1f}% {r["sharpe_ratio"]:>6.2f} '
          f'{r["win_rate"]*100:>6.1f}% {r["total_trades"]:>6} {pf:>7.2f}')

# ============================================================
# 2. MA参数优化
# ============================================================
print(f'\n二、MA交叉参数优化')
print(f'{"快线":>4} {"慢线":>4} {"总收益%":>9} {"年化%":>9} {"夏普":>6} {"最大回撤%":>8} {"胜率%":>7} {"交易":>5}')
print(f'{"-"*60}')
best_params = None
best_annual = -999
for fast in [3, 5, 10, 15, 20]:
    for slow in [10, 15, 20, 30, 45, 60]:
        if fast >= slow: continue
        r = run_backtest(StrategyType.MA_CROSS, {'fast_ma': fast, 'slow_ma': slow}, klines)
        tr = r['total_return'] * 100
        ar = r['annual_return'] * 100
        print(f'{fast:>4} {slow:>4} {tr:>+8.1f}% {ar:>+8.1f}% {r["sharpe_ratio"]:>6.2f} '
              f'{r["max_drawdown"]*100:>7.1f}% {r["win_rate"]*100:>6.1f}% {r["total_trades"]:>5}')
        if ar > best_annual:
            best_annual = ar
            best_params = (fast, slow, r)

if best_params:
    f, s, br = best_params
    print(f'\n最优参数: MA{f}/{s}  年化 {br["annual_return"]*100:+.1f}%  夏普 {br["sharpe_ratio"]:.2f}')

# ============================================================
# 3. 最佳策略权曲线 & 交易明细
# ============================================================
print(f'\n三、MA交叉(5,20) 最近20笔交易明细')
r = run_backtest(StrategyType.MA_CROSS, {'fast_ma': 5, 'slow_ma': 20}, klines)
all_trades = r.get('trades', [])
recent = all_trades[-20:] if len(all_trades) > 20 else all_trades
for t in recent:
    ts = datetime.fromtimestamp(t['timestamp']).strftime('%Y-%m-%d')
    action = t['action']
    if action == 'buy':
        print(f'  {ts}  买入  @{t["price"]:.2f}  x{t["quantity"]:.0f}')
    else:
        pnl = t.get('pnl', 0)
        print(f'  {ts}  卖出  @{t["price"]:.2f}  x{t["quantity"]:.0f}  盈亏: {pnl:+.2f}  '
              f'收益率: {pnl/(t["quantity"]*t["price"]-pnl)*100 if t["price"] and t["quantity"] else 0:+.1f}%')

# ============================================================
# 4. 分年收益率
# ============================================================
print(f'\n四、分年度收益率 (MA5/20)')
year_trades = {}
for t in all_trades:
    yr = datetime.fromtimestamp(t['timestamp']).year
    if yr not in year_trades:
        year_trades[yr] = []
    year_trades[yr].append(t)

for yr in sorted(year_trades.keys()):
    sells = [t for t in year_trades[yr] if t['action'] == 'sell' and t.get('pnl') is not None]
    if not sells: continue
    total_pnl = sum(t['pnl'] for t in sells)
    wins = sum(1 for t in sells if t['pnl'] > 0)
    print(f'  {yr}: {len(sells)}笔卖出 胜{wins}次  净盈亏: {total_pnl:+.0f}')

# ============================================================
# 5. 最近一年回测
# ============================================================
print(f'\n五、近一年回测 (MA5/20)')
one_year_ago = int(datetime.now().timestamp()) - 365*86400
recent_klines = [k for k in klines if k['timestamp'] >= one_year_ago]
if len(recent_klines) < 60:
    recent_klines = klines[-252:]
print(f'  使用最近 {len(recent_klines)} 条K线')
r1 = run_backtest(StrategyType.MA_CROSS, {'fast_ma': 5, 'slow_ma': 20}, recent_klines)
trades_1y = [t for t in r1.get('trades', []) if t['action'] == 'sell' and t.get('pnl') is not None]
wins_1y = sum(1 for t in trades_1y if t['pnl'] > 0)
total_pnl_1y = sum(t['pnl'] for t in trades_1y)
print(f'  总收益: {r1["total_return"]*100:+.1f}%  年化: {r1["annual_return"]*100:+.1f}%')
print(f'  夏普: {r1["sharpe_ratio"]:.2f}  最大回撤: {r1["max_drawdown"]*100:.1f}%')
print(f'  交易: {r1["total_trades"]}笔  胜率: {r1["win_rate"]*100:.0f}%  净盈亏: {total_pnl_1y:+.0f}')

for t in r1.get('trades', []):
    ts = datetime.fromtimestamp(t['timestamp']).strftime('%Y-%m-%d')
    if t['action'] == 'buy':
        print(f'    {ts}  买入  @{t["price"]:.2f}')
    else:
        print(f'    {ts}  卖出  @{t["price"]:.2f}  盈亏: {t.get("pnl",0):+.0f}')

print(f'\n{"="*70}')
print(f'  报告结束')
print(f'{"="*70}')
