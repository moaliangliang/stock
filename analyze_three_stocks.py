"""
Analyze 招商银行, 鲁抗医药, 长城汽车 using project backtest engine.
"""
import pandas as pd
import numpy as np
import json, sys, os, math
from datetime import datetime, timezone

# Add project path
sys.path.insert(0, '/root/workspace/stock/backend')

stocks_info = {
    "招商银行": {
        "code": "600036.SH",
        "path": "/root/.openclaw/workspace/mx_data/output/mx_data_招商银行_日K线_2025-01-01_2026-05-10.xlsx",
    },
    "鲁抗医药": {
        "code": "600789.SH",
        "path": "/root/.openclaw/workspace/mx_data/output/mx_data_鲁抗医药_日K线_2025-01-01_2026-05-10.xlsx",
    },
    "长城汽车": {
        "code": "601633.SH",
        "path": "/root/.openclaw/workspace/mx_data/output/mx_data_长城汽车_日K线_2025-01-01_2026-05-10.xlsx",
    },
}

def parse_price(s):
    """Parse price string like '38.29元' or '47.78港元'"""
    if isinstance(s, (int, float)):
        return float(s)
    s = str(s).replace('元', '').replace('港元', '').replace(',', '').strip()
    return float(s)

def parse_pct(s):
    """Parse percentage string like '-0.02635%' or '1.195%'"""
    if isinstance(s, (int, float)):
        return float(s)
    return float(str(s).replace('%', '').strip())

def parse_vol(s):
    """Parse volume string like '8695万股' or '1.56亿股'"""
    if isinstance(s, (int, float)):
        return float(s)
    s = str(s).replace('股', '').strip()
    if '亿' in s:
        return float(s.replace('亿', '')) * 1e8
    elif '万' in s:
        return float(s.replace('万', '')) * 1e4
    return float(s)

def reconstruct_kline_data(name, code, path):
    """Reconstruct daily OHLCV from mx-data Excel output."""
    xl = pd.ExcelFile(path)

    # Find relevant sheets
    ohlc_sheet = [s for s in xl.sheet_names if '开盘价' in s and '收盘价' in s and code in s]
    boll_sheet = [s for s in xl.sheet_names if 'BOLL' in s and code in s]
    range_sheet = [s for s in xl.sheet_names if '涨跌幅' in s and '换手率' in s and code in s]

    if not (ohlc_sheet and boll_sheet and range_sheet):
        print(f"  WARNING: Missing sheets for {name}")
        return []

    # Load daily data
    df_range = pd.read_excel(path, sheet_name=range_sheet[0])
    df_boll = pd.read_excel(path, sheet_name=boll_sheet[0])
    df_ohlc = pd.read_excel(path, sheet_name=ohlc_sheet[0])

    # Parse monthly OHLC
    monthly = {}
    for _, row in df_ohlc.iterrows():
        date_str = str(row['date'])
        # Extract YYYY-MM from '2026-05-08(月)' format
        ym = date_str[:7]
        monthly[ym] = {
            'open': parse_price(row['开盘价']),
            'close': parse_price(row['收盘价']),
        }

    # Sort by month ascending
    sorted_months = sorted(monthly.keys())

    # Merge daily data on date
    df_boll['date_str'] = df_boll['date'].astype(str).str[:10]
    df_range['date_str'] = df_range['date'].astype(str).str[:10]

    # Merge BOLL and range data
    df = pd.merge(df_boll, df_range, on='date_str', how='inner')
    df = df.sort_values('date_str').reset_index(drop=True)

    # Parse daily returns
    df['daily_return'] = df['区间涨跌幅'].apply(parse_pct) / 100.0
    df['volume'] = df['区间成交量'].apply(parse_vol)
    df['bb_low'] = pd.to_numeric(df['BOLL布林线LOW'], errors='coerce')
    df['bb_mid'] = pd.to_numeric(df['BOLL布林线'], errors='coerce')
    df['bb_up'] = pd.to_numeric(df['BOLL布林线UP'], errors='coerce')
    df['turnover'] = df['区间换手率'].apply(parse_pct)

    # Reconstruct daily close prices
    # Use the last monthly close as anchor, work backwards using daily returns
    last_month_close = monthly[sorted_months[-1]]['close']

    closes = []
    n = len(df)
    # We'll compute from the last date backwards
    # close[i-1] = close[i] / (1 + daily_return[i])

    # First, find where each month's last trading day is
    df['ym'] = df['date_str'].str[:7]

    # Compute closes forward from first known monthly close
    # Strategy: set first close from monthly, then propagate forward
    first_ym = sorted_months[0]
    current_close = monthly[first_ym]['close']

    # Find all rows for first_ym and set the last day's close to the monthly close
    for i in range(n):
        ym = df.iloc[i]['ym']
        if i > 0 and df.iloc[i]['ym'] != df.iloc[i-1]['ym']:
            # Month changed, check if we have a monthly close for previous month
            prev_ym = df.iloc[i-1]['ym']
            if prev_ym in monthly:
                current_close = monthly[prev_ym]['close']

        # Apply daily return forward
        if i == 0:
            # First bar uses monthly open
            current_close = monthly.get(df.iloc[i]['ym'], {}).get('close', current_close)

        ret = df.iloc[i]['daily_return']
        # Close = Open * (1 + return), so if we know close and return, we need open
        # Actually: return = (close - prev_close) / prev_close
        # So: close = prev_close * (1 + return)
        # We already have current_close from previous iteration as prev_close
        next_close = current_close * (1 + ret)
        closes.append(next_close)
        current_close = next_close

    df['close'] = closes

    # Estimate high/low from BOLL bands
    # Use half bandwidth: (BB_up - BB_low) / (4 * BB_mid) as ratio
    for i in range(n):
        bb_up = df.iloc[i]['bb_up']
        bb_low = df.iloc[i]['bb_low']
        bb_mid = df.iloc[i]['bb_mid']
        close = df.iloc[i]['close']

        if bb_mid > 0 and bb_up > bb_low:
            half_band_ratio = (bb_up - bb_low) / (2 * bb_mid) * 0.5
        else:
            half_band_ratio = 0.01

        # Scale to be reasonable: half_band_ratio is ~2-5% typically
        half_band_ratio = min(half_band_ratio, 0.05)

        df.at[i, 'high'] = close * (1 + half_band_ratio)
        df.at[i, 'low'] = close * (1 - half_band_ratio)

    # Open = previous close (standard for daily bars)
    opens = []
    for i in range(n):
        if i == 0:
            first_ym_key = df.iloc[0]['ym']
            opens.append(monthly.get(first_ym_key, {}).get('open', df.iloc[0]['close']))
        else:
            opens.append(df.iloc[i-1]['close'])
    df['open'] = opens

    # Build kline data list
    kline_data = []
    for i in range(n):
        ts = datetime.strptime(df.iloc[i]['date_str'], '%Y-%m-%d')
        kline_data.append({
            'timestamp': int(ts.timestamp()),
            'open': round(float(df.iloc[i]['open']), 2),
            'high': round(float(df.iloc[i]['high']), 2),
            'low': round(float(df.iloc[i]['low']), 2),
            'close': round(float(df.iloc[i]['close']), 2),
            'volume': round(float(df.iloc[i]['volume']), 2),
        })

    return kline_data


# ---------------------------------------------------------------------------
# Run backtests
# ---------------------------------------------------------------------------
from app.services.backtest import run_backtest
from app.models.strategy import StrategyType

print("=" * 80)
print("三只股票回测分析：招商银行(600036)、鲁抗医药(600789)、长城汽车(601633)")
print("=" * 80)

all_results = {}

for name, info in stocks_info.items():
    print(f"\n{'='*60}")
    print(f"  {name} ({info['code']})")
    print(f"{'='*60}")

    kline_data = reconstruct_kline_data(name, info['code'], info['path'])
    print(f"  重建K线数据: {len(kline_data)} 条")

    if not kline_data:
        print("  SKIP: No data available")
        continue

    # Get price range for display
    closes = [k['close'] for k in kline_data]
    first_close = closes[0]
    last_close = closes[-1]
    buy_hold_return = (last_close - first_close) / first_close * 100
    print(f"  价格范围: {min(closes):.2f} - {max(closes):.2f}")
    print(f"  起止: {first_close:.2f} → {last_close:.2f} (买入持有: {buy_hold_return:+.2f}%)")

    # Grid price range
    grid_upper = max(closes) * 0.95
    grid_lower = min(closes) * 1.05
    print(f"  网格区间: {grid_lower:.2f} - {grid_upper:.2f}")

    stock_results = {}

    # Test each strategy
    strategies = [
        ("MA_CROSS", StrategyType.MA_CROSS, {"fast_period": 5, "slow_period": 20}),
        ("MACD", StrategyType.MACD, {"fast": 12, "slow": 26, "signal": 9}),
        ("KDJ", StrategyType.KDJ, {"n": 9, "k": 3, "d": 3}),
        ("BOLLINGER", StrategyType.BOLLINGER, {"period": 20, "std": 2.0}),
        ("GRID", StrategyType.GRID, {"grid_levels": 10, "upper_price": grid_upper, "lower_price": grid_lower}),
    ]

    for sname, stype, sparams in strategies:
        try:
            result = run_backtest(
                strategy_type=stype,
                params=sparams,
                kline_data=kline_data,
                initial_capital=100000.0,
                commission=0.0003,
                slippage=0.001,
            )
            total_ret_pct = result['total_return'] * 100
            win_rate_pct = result['win_rate'] * 100
            print(f"  {sname:12s}: 收益 {total_ret_pct:+7.2f}% | 年化 {result['annual_return']*100:+7.2f}% | "
                  f"夏普 {result['sharpe_ratio']:6.2f} | 胜率 {win_rate_pct:5.1f}% | "
                  f"回撤 {result['max_drawdown']*100:5.2f}% | 交易 {result['total_trades']:3d}次 | "
                  f"盈亏比 {result['profit_factor']:.2f}")

            stock_results[sname] = {
                'total_return': result['total_return'],
                'annual_return': result['annual_return'],
                'sharpe_ratio': result['sharpe_ratio'],
                'win_rate': result['win_rate'],
                'max_drawdown': result['max_drawdown'],
                'total_trades': result['total_trades'],
                'profit_trades': result['profit_trades'],
                'loss_trades': result['loss_trades'],
                'profit_factor': result['profit_factor'],
                'final_equity': result['final_equity'],
            }
        except Exception as e:
            print(f"  {sname:12s}: ERROR - {e}")
            stock_results[sname] = {'error': str(e)}

    all_results[name] = {
        'code': info['code'],
        'buy_hold_return': buy_hold_return,
        'data_points': len(kline_data),
        'first_close': first_close,
        'last_close': last_close,
        'min_close': min(closes),
        'max_close': max(closes),
        'grid_lower': grid_lower,
        'grid_upper': grid_upper,
        'strategies': stock_results,
    }

# Save results
with open('/root/workspace/stock/three_stocks_results.json', 'w') as f:
    json.dump(all_results, f, ensure_ascii=False, indent=2, default=str)

print("\n\nResults saved to three_stocks_results.json")
print("Done!")
