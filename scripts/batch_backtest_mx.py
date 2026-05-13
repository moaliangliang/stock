#!/usr/bin/env python3
"""批量回测脚本 - 使用MX数据API获取K线，对10只股票跑多个策略"""

import os, sys, json, re
import numpy as np
import requests
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Tuple

API_KEY = os.getenv("MX_APIKEY", "mkt_QwXsdfMhLbTFxN2LPAovHmtFuUIGLfrOeCGJroTsY8k")
BASE_URL = "https://mkapi2.dfcfs.com/finskillshub/api/claw/query"

STOCKS = [
    ("000528", "柳工"),
    ("002997", "瑞鹄模具"),
    ("300505", "川金诺"),
    ("600563", "法拉电子"),
    ("002768", "国恩股份"),
    ("000950", "重药控股"),
    ("600406", "国电南瑞"),
    ("300059", "东方财富"),
    ("000997", "新大陆"),
    ("600089", "特变电工"),
]

def query_mx_data(query_text: str) -> dict:
    headers = {"Content-Type": "application/json", "apikey": API_KEY}
    resp = requests.post(BASE_URL, headers=headers, json={"toolQuery": query_text}, timeout=30)
    resp.raise_for_status()
    return resp.json()

def parse_kline(result: dict) -> dict:
    """从MX API返回中提取日K线 {date_str: {close, pct_change}}"""
    dto_list = result.get("data",{}).get("data",{}).get("searchDataResultDTO",{}).get("dataTableDTOList",[])
    kline = {}
    for dto in dto_list:
        table = dto.get("table", {})
        headers = table.get("headName", [])
        if len(headers) <= 1:
            continue  # skip single-row tables (current quote)

        # Find the close price and pct_change keys
        name_map = dto.get("nameMap", {})
        close_key, pct_key = None, None
        for k in table.keys():
            if k == "headName":
                continue
            cn = name_map.get(k, name_map.get(str(k), ""))
            if "收盘" in cn or "最新价" in cn:
                close_key = k
            elif "涨跌幅" in cn:
                pct_key = k

        if close_key is None:
            # fallback: first numeric key
            for k in table.keys():
                if k != "headName" and k.isdigit():
                    close_key = k
                    break

        vals_close = table.get(close_key, []) if close_key else []
        vals_pct = table.get(pct_key, []) if pct_key else []

        for i, date_raw in enumerate(headers):
            date_str = re.sub(r'\(.*\)', '', str(date_raw)).strip()
            if i < len(vals_close):
                close_val = str(vals_close[i]).replace("元","").replace("%","").strip()
                pct_val = str(vals_pct[i]).replace("%","").strip() if i < len(vals_pct) else "0"
                try:
                    kline[date_str] = {"close": float(close_val), "pct_change": float(pct_val)}
                except ValueError:
                    continue
    return kline

def compute_metrics(daily_returns: np.ndarray, trades: List[Dict], initial_cap: float, final_equity: float) -> Dict:
    total_return = (final_equity - initial_cap) / initial_cap * 100
    n = max(len(daily_returns), 1)
    annual_return = ((1 + total_return/100) ** (252/n) - 1) * 100 if total_return > -100 else -100

    # Max drawdown from daily returns
    cumulative = np.cumprod(1 + daily_returns)
    peak = np.maximum.accumulate(cumulative)
    dd = (peak - cumulative) / peak
    max_dd = float(np.max(dd) * 100) if len(dd) > 0 else 0

    sharpe = float(np.sqrt(252) * daily_returns.mean() / daily_returns.std()) if len(daily_returns) > 1 and daily_returns.std() > 0 else 0

    total_trades = len(trades)
    profit_trades = [t for t in trades if t["pnl"] > 0]
    loss_trades = [t for t in trades if t["pnl"] <= 0]
    win_rate = len(profit_trades) / total_trades * 100 if total_trades > 0 else 0
    gross_profit = sum(t["pnl"] for t in profit_trades) if profit_trades else 0
    gross_loss = abs(sum(t["pnl"] for t in loss_trades)) if loss_trades else 0
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else (99.99 if profit_trades else 0)

    return {
        "total_return": round(total_return, 2), "annual_return": round(annual_return, 2),
        "max_drawdown": round(max_dd, 2), "sharpe_ratio": round(sharpe, 2),
        "win_rate": round(win_rate, 1), "total_trades": total_trades,
        "profit_trades": len(profit_trades), "loss_trades": len(loss_trades),
        "profit_factor": round(profit_factor, 2),
    }

def run_ma_strategy(close_prices: np.ndarray, fast: int, slow: int, initial_cap: float = 100000):
    """双均线策略 - 返回 (daily_returns, trades, final_equity)"""
    n = len(close_prices)
    if n <= slow:
        return np.array([]), [], initial_cap

    ma_fast = np.array([np.mean(close_prices[max(0,i-fast+1):i+1]) for i in range(n)])
    ma_slow = np.array([np.mean(close_prices[max(0,i-slow+1):i+1]) for i in range(n)])

    position = 0
    cash = initial_cap
    daily_values = []
    trades = []

    for i in range(slow, n):
        price = close_prices[i]
        prev_fast, prev_slow = ma_fast[i-1], ma_slow[i-1]
        curr_fast, curr_slow = ma_fast[i], ma_slow[i]

        if prev_fast <= prev_slow and curr_fast > curr_slow and position == 0:
            position = cash / price
            entry_price, entry_idx = price, i
            cash = 0
        elif prev_fast >= prev_slow and curr_fast < curr_slow and position > 0:
            pnl = (price - entry_price) * position
            cash = position * price
            trades.append({"pnl": pnl, "entry_idx": entry_idx, "exit_idx": i})
            position = 0

        daily_values.append(cash + position * price)

    if position > 0:
        final_price = close_prices[-1]
        pnl = (final_price - entry_price) * position
        trades.append({"pnl": pnl, "entry_idx": entry_idx, "exit_idx": n-1})
        daily_values.append(position * final_price)

    if len(daily_values) < 2:
        return np.array([]), trades, initial_cap

    daily_values = np.array(daily_values)
    daily_returns = np.diff(daily_values) / daily_values[:-1]
    final_equity = daily_values[-1]
    return daily_returns, trades, final_equity

def run_momentum_strategy(close_prices: np.ndarray, lookback: int, hold: int, initial_cap: float = 100000):
    """突破N日高点买入，持有M日卖出"""
    n = len(close_prices)
    if n <= lookback + 1:
        return np.array([]), [], initial_cap

    position = 0
    cash = initial_cap
    daily_values = []
    trades = []
    hold_counter = 0

    for i in range(lookback, n):
        price = close_prices[i]
        n_day_high = np.max(close_prices[i-lookback:i])

        if position > 0:
            hold_counter += 1

        if price > n_day_high and position == 0:
            position = cash / price
            entry_price, entry_idx = price, i
            cash = 0
            hold_counter = 0
        elif position > 0 and hold_counter >= hold:
            pnl = (price - entry_price) * position
            cash = position * price
            trades.append({"pnl": pnl, "entry_idx": entry_idx, "exit_idx": i})
            position = 0

        daily_values.append(cash + position * price)

    if position > 0:
        final_price = close_prices[-1]
        pnl = (final_price - entry_price) * position
        trades.append({"pnl": pnl, "entry_idx": entry_idx, "exit_idx": n-1})
        daily_values.append(position * final_price)

    if len(daily_values) < 2:
        return np.array([]), trades, initial_cap

    daily_values = np.array(daily_values)
    daily_returns = np.diff(daily_values) / daily_values[:-1]
    final_equity = daily_values[-1]
    return daily_returns, trades, final_equity

def run_rsi_strategy(close_prices: np.ndarray, period: int = 14, oversold: int = 30, overbought: int = 70, initial_cap: float = 100000):
    """RSI均值回归策略"""
    n = len(close_prices)
    if n <= period + 1:
        return np.array([]), [], initial_cap

    deltas = np.diff(close_prices, prepend=close_prices[0])
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)

    rsi = np.zeros(n)
    for i in range(period, n):
        avg_gain = np.mean(gains[i-period+1:i+1])
        avg_loss = np.mean(losses[i-period+1:i+1])
        if avg_loss == 0:
            rsi[i] = 100
        else:
            rsi[i] = 100 - 100 / (1 + avg_gain / avg_loss)

    position = 0
    cash = initial_cap
    daily_values = []
    trades = []

    for i in range(period, n):
        price = close_prices[i]
        r = rsi[i]

        if r < oversold and r > 0 and position == 0:
            position = cash / price
            entry_price, entry_idx = price, i
            cash = 0
        elif r > overbought and position > 0:
            pnl = (price - entry_price) * position
            cash = position * price
            trades.append({"pnl": pnl, "entry_idx": entry_idx, "exit_idx": i})
            position = 0

        daily_values.append(cash + position * price)

    if position > 0:
        final_price = close_prices[-1]
        pnl = (final_price - entry_price) * position
        trades.append({"pnl": pnl, "entry_idx": entry_idx, "exit_idx": n-1})
        daily_values.append(position * final_price)

    if len(daily_values) < 2:
        return np.array([]), trades, initial_cap

    daily_values = np.array(daily_values)
    daily_returns = np.diff(daily_values) / daily_values[:-1]
    final_equity = daily_values[-1]
    return daily_returns, trades, final_equity


def main():
    print(f"\n{'='*100}")
    print(f"  批量回测报告 - {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*100}\n")

    all_results = {}

    for symbol, name in STOCKS:
        print(f"[{symbol} {name}] 获取K线数据...", end=" ", flush=True)
        try:
            result = query_mx_data(f"{name} {symbol} 近250个交易日每日收盘价 涨跌幅")
            kline = parse_kline(result)
        except Exception as e:
            print(f"FAILED: {e}")
            continue

        if len(kline) < 50:
            print(f"FAILED: 仅{len(kline)}根K线")
            continue

        dates = sorted(kline.keys())
        close_prices = np.array([kline[d]["close"] for d in dates])
        print(f"OK ({len(close_prices)}根K线, {dates[0]} ~ {dates[-1]})")

        strategies = {
            "MA_5_20": run_ma_strategy(close_prices, 5, 20),
            "MA_10_30": run_ma_strategy(close_prices, 10, 30),
            "Momentum_20_5": run_momentum_strategy(close_prices, 20, 5),
            "Momentum_30_10": run_momentum_strategy(close_prices, 30, 10),
            "RSI_14_30_70": run_rsi_strategy(close_prices, 14, 30, 70),
        }

        # Buy & hold
        bh_return = (close_prices[-1] / close_prices[0] - 1) * 100
        bh_daily = np.diff(close_prices) / close_prices[:-1]
        # Simple BH annual return
        days = len(close_prices)
        bh_annual = ((1 + bh_return/100) ** (252/days) - 1) * 100

        # Compute drawdown for BH
        cum_bh = close_prices / close_prices[0]
        peak_bh = np.maximum.accumulate(cum_bh)
        dd_bh = (peak_bh - cum_bh) / peak_bh
        bh_max_dd = float(np.max(dd_bh) * 100)

        # Sharpe for BH
        bh_sharpe = float(np.sqrt(252) * bh_daily.mean() / bh_daily.std()) if len(bh_daily) > 1 and bh_daily.std() > 0 else 0

        print(f"  {'策略':<20} {'总收益%':>8} {'年化%':>8} {'最大回撤%':>9} {'夏普':>6} {'胜率%':>7} {'交易数':>6} {'盈亏比':>7}")
        print(f"  {'-'*80}")

        stock_results = {}
        for sn, (rets, trades, final_eq) in strategies.items():
            if len(rets) == 0:
                continue
            m = compute_metrics(rets, trades, 100000, final_eq)
            stock_results[sn] = m
            print(f"  {sn:<20} {m['total_return']:>7.2f} {m['annual_return']:>7.2f} "
                  f"{m['max_drawdown']:>8.2f} {m['sharpe_ratio']:>5.2f} {m['win_rate']:>6.1f} "
                  f"{m['total_trades']:>5d} {m['profit_factor']:>6.2f}")

        # Buy & hold
        print(f"  {'Buy_Hold':<20} {bh_return:>7.2f} {bh_annual:>7.2f} "
              f"{bh_max_dd:>8.2f} {bh_sharpe:>5.2f} {'-':>6} {'-':>6} {'-':>6}")

        stock_results["Buy_Hold"] = {"total_return": round(bh_return,2), "annual_return": round(bh_annual,2),
                                      "max_drawdown": round(bh_max_dd,2), "sharpe_ratio": round(bh_sharpe,2),
                                      "win_rate": 0, "total_trades": 1, "profit_factor": 0}
        all_results[f"{symbol} {name}"] = stock_results

    # ==================== 综合排名 ====================
    print(f"\n{'='*100}")
    print(f"  综合排名 (按MA_5_20策略年化收益排序)")
    print(f"{'='*100}")
    print(f"  {'排名':<4} {'股票':<22} {'策略':<18} {'总收益%':>8} {'年化%':>8} {'最大回撤%':>9} {'夏普':>6} {'胜率%':>7} {'交易数':>6} {'盈亏比':>7}")

    ranking = []
    for stock_name, strategies in all_results.items():
        for sn, sm in strategies.items():
            if sn in ("Buy_Hold",):
                ranking.append((stock_name, sn, sm))

    ranking.sort(key=lambda x: x[2]["total_return"], reverse=True)

    for rank, (stock_name, sn, sm) in enumerate(ranking, 1):
        emoji = "🟢" if sm["total_return"] > 20 else ("🟡" if sm["total_return"] > 0 else "🔴")
        wr = f"{sm['win_rate']:.1f}" if sm['win_rate'] > 0 else "-"
        pf = f"{sm['profit_factor']:.2f}" if sm['profit_factor'] > 0 else "-"
        print(f"  {emoji} #{rank:<2d} {stock_name:<22} {sn:<18} {sm['total_return']:>7.2f} "
              f"{sm['annual_return']:>7.2f} {sm['max_drawdown']:>8.2f} {sm['sharpe_ratio']:>5.2f} "
              f"{wr:>6} {sm['total_trades']:>5d} {pf:>6}")

    # ==================== 综合评分 ====================
    print(f"\n{'='*100}")
    print(f"  综合评分 (年化收益40% + 夏普30% - 最大回撤30%)")
    print(f"{'='*100}")

    scores = {}
    for stock_name, strategies in all_results.items():
        # Average across MA strategies + Momentum
        key_strats = ["MA_5_20", "MA_10_30", "Momentum_20_5"]
        ann_returns = [strategies[s]["annual_return"] for s in key_strats if s in strategies]
        sharpes = [strategies[s]["sharpe_ratio"] for s in key_strats if s in strategies]
        drawdowns = [strategies[s]["max_drawdown"] for s in key_strats if s in strategies]

        if not ann_returns:
            continue

        avg_ann = np.mean(ann_returns)
        avg_sharpe = np.mean(sharpes)
        avg_dd = np.mean(drawdowns)
        score = avg_ann * 0.4 + avg_sharpe * 10 * 0.3 - avg_dd * 0.3
        scores[stock_name] = {
            "score": round(score, 2), "avg_ann": round(avg_ann, 2),
            "avg_sharpe": round(avg_sharpe, 2), "avg_dd": round(avg_dd, 2),
            "bh_return": strategies.get("Buy_Hold", {}).get("total_return", 0),
        }

    print(f"  {'排名':<4} {'股票':<22} {'综合分':>7} {'均年化%':>8} {'均夏普':>7} {'均回撤%':>8} {'买入持有%':>9}")
    print(f"  {'-'*72}")
    for rank, (stock_name, sc) in enumerate(sorted(scores.items(), key=lambda x: x[1]["score"], reverse=True), 1):
        emoji = "⭐" if rank <= 3 else ("✅" if rank <= 7 else "⚪")
        print(f"  {emoji} #{rank:<2d} {stock_name:<22} {sc['score']:>7.2f} {sc['avg_ann']:>7.2f} "
              f"{sc['avg_sharpe']:>6.2f} {sc['avg_dd']:>7.2f} {sc['bh_return']:>8.2f}")


if __name__ == "__main__":
    main()
