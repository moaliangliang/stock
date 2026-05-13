#!/usr/bin/env python3
"""批量回测脚本 - 对多只股票跑多个策略并比较"""

import numpy as np
import pandas as pd
import akshare as ak
from datetime import datetime, timedelta
from typing import Dict, List, Tuple
import json

STOCKS = {
    "000528": "柳工",
    "002997": "瑞鹄模具",
    "300505": "川金诺",
    "600563": "法拉电子",
    "002768": "国恩股份",
    "000950": "重药控股",
    "600406": "国电南瑞",
    "300059": "东方财富",
    "000997": "新大陆",
    "600089": "特变电工",
}

def fetch_kline(symbol: str, days: int = 250) -> pd.DataFrame:
    """获取个股日K线数据"""
    try:
        df = ak.stock_zh_a_hist(
            symbol=symbol,
            period="daily",
            start_date=(datetime.now() - timedelta(days=days)).strftime("%Y%m%d"),
            end_date=datetime.now().strftime("%Y%m%d"),
            adjust="qfq",  # 前复权
        )
        if df is None or df.empty:
            return pd.DataFrame()
        df = df.rename(columns={
            "日期": "date", "开盘": "open", "收盘": "close",
            "最高": "high", "最低": "low", "成交量": "volume",
            "成交额": "amount", "振幅": "amplitude", "涨跌幅": "pct_change"
        })
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)
        return df
    except Exception as e:
        print(f"  [ERROR] fetch_kline({symbol}): {e}")
        return pd.DataFrame()


def compute_metrics(equity_curve: np.ndarray, trades: List[Dict], initial_capital: float) -> Dict:
    """计算回测绩效指标"""
    if len(equity_curve) < 2:
        return {
            "total_return": 0, "annual_return": 0, "max_drawdown": 0,
            "sharpe_ratio": 0, "win_rate": 0, "total_trades": 0,
            "profit_trades": 0, "loss_trades": 0, "profit_factor": 0,
        }

    total_return = (equity_curve[-1] - initial_capital) / initial_capital

    # 年化收益率
    trading_days = len(equity_curve)
    annual_return = (1 + total_return) ** (252 / max(trading_days, 1)) - 1

    # 最大回撤
    peak = np.maximum.accumulate(equity_curve)
    drawdown = (peak - equity_curve) / peak
    max_drawdown = float(np.max(drawdown))

    # 夏普比率
    daily_returns = np.diff(equity_curve) / equity_curve[:-1]
    if len(daily_returns) > 1 and daily_returns.std() > 0:
        sharpe = float(np.sqrt(252) * daily_returns.mean() / daily_returns.std())
    else:
        sharpe = 0.0

    # 交易统计
    total_trades = len(trades)
    profit_trades = [t for t in trades if t.get("pnl", 0) > 0]
    loss_trades = [t for t in trades if t.get("pnl", 0) <= 0]
    win_rate = len(profit_trades) / total_trades if total_trades > 0 else 0
    profit_factor = (
        sum(t["pnl"] for t in profit_trades) / abs(sum(t["pnl"] for t in loss_trades))
        if loss_trades and abs(sum(t["pnl"] for t in loss_trades)) > 0
        else (float("inf") if profit_trades else 0)
    )

    return {
        "total_return": round(total_return * 100, 2),
        "annual_return": round(annual_return * 100, 2),
        "max_drawdown": round(max_drawdown * 100, 2),
        "sharpe_ratio": round(sharpe, 2),
        "win_rate": round(win_rate * 100, 2),
        "total_trades": total_trades,
        "profit_trades": len(profit_trades),
        "loss_trades": len(loss_trades),
        "profit_factor": round(profit_factor, 2) if profit_factor != float("inf") else 99.99,
    }


def backtest_ma_crossover(df: pd.DataFrame, fast: int = 5, slow: int = 20,
                          initial_capital: float = 100000) -> Tuple[np.ndarray, List[Dict]]:
    """双均线交叉策略"""
    df = df.copy()
    df["ma_fast"] = df["close"].rolling(fast).mean()
    df["ma_slow"] = df["close"].rolling(slow).mean()

    cash = initial_capital
    position = 0
    equity = []
    trades = []
    open_trade = None

    for i in range(slow, len(df)):
        price = df["close"].iloc[i]
        prev_fast = df["ma_fast"].iloc[i - 1]
        prev_slow = df["ma_slow"].iloc[i - 1]
        curr_fast = df["ma_fast"].iloc[i]
        curr_slow = df["ma_slow"].iloc[i]

        # 金叉买入
        if prev_fast <= prev_slow and curr_fast > curr_slow and position == 0:
            position = cash / price * 0.95
            open_trade = {"entry_price": price, "entry_date": df["date"].iloc[i], "shares": position}
            cash *= 0.05
        # 死叉卖出
        elif prev_fast >= prev_slow and curr_fast < curr_slow and position > 0:
            pnl = (price - open_trade["entry_price"]) * open_trade["shares"]
            cash = position * price
            trades.append({"entry_date": open_trade["entry_date"], "exit_date": df["date"].iloc[i],
                           "entry_price": open_trade["entry_price"], "exit_price": price, "pnl": pnl})
            position = 0
            open_trade = None

        equity.append(cash + position * price)

    # 清仓
    if position > 0 and open_trade:
        last_price = df["close"].iloc[-1]
        pnl = (last_price - open_trade["entry_price"]) * open_trade["shares"]
        trades.append({"entry_date": open_trade["entry_date"], "exit_date": df["date"].iloc[-1],
                       "entry_price": open_trade["entry_price"], "exit_price": last_price, "pnl": pnl})
        equity.append(cash + position * last_price)

    return np.array(equity) if equity else np.array([initial_capital]), trades


def backtest_momentum(df: pd.DataFrame, lookback: int = 20, hold_days: int = 5,
                      initial_capital: float = 100000) -> Tuple[np.ndarray, List[Dict]]:
    """动量突破策略 - 突破N日高点买入，持有M天卖出"""
    df = df.copy()
    df["high_n"] = df["high"].rolling(lookback).max().shift(1)

    cash = initial_capital
    position = 0
    equity = []
    trades = []
    hold_counter = 0
    open_trade = None

    for i in range(lookback + 1, len(df)):
        price = df["close"].iloc[i]

        if position > 0:
            hold_counter += 1

        # 突破买入
        if df["high"].iloc[i] > df["high_n"].iloc[i] and position == 0:
            position = cash / price * 0.95
            open_trade = {"entry_price": price, "entry_date": df["date"].iloc[i], "shares": position}
            cash *= 0.05
            hold_counter = 0
        # 持有到期卖出
        elif position > 0 and hold_counter >= hold_days:
            pnl = (price - open_trade["entry_price"]) * open_trade["shares"]
            cash = position * price
            trades.append({"entry_date": open_trade["entry_date"], "exit_date": df["date"].iloc[i],
                           "entry_price": open_trade["entry_price"], "exit_price": price, "pnl": pnl})
            position = 0
            open_trade = None

        equity.append(cash + position * price)

    if position > 0 and open_trade:
        last_price = df["close"].iloc[-1]
        pnl = (last_price - open_trade["entry_price"]) * open_trade["shares"]
        trades.append({"entry_date": open_trade["entry_date"], "exit_date": df["date"].iloc[-1],
                       "entry_price": open_trade["entry_price"], "exit_price": last_price, "pnl": pnl})
        equity.append(cash + position * last_price)

    return np.array(equity) if equity else np.array([initial_capital]), trades


def backtest_rsi_mean_reversion(df: pd.DataFrame, period: int = 14,
                                oversold: int = 30, overbought: int = 70,
                                initial_capital: float = 100000) -> Tuple[np.ndarray, List[Dict]]:
    """RSI均值回归策略"""
    df = df.copy()
    delta = df["close"].diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    rs = avg_gain / avg_loss
    df["rsi"] = 100 - (100 / (1 + rs))

    cash = initial_capital
    position = 0
    equity = []
    trades = []
    open_trade = None

    for i in range(period + 1, len(df)):
        price = df["close"].iloc[i]
        rsi = df["rsi"].iloc[i]

        # 超卖买入
        if rsi < oversold and position == 0:
            position = cash / price * 0.95
            open_trade = {"entry_price": price, "entry_date": df["date"].iloc[i], "shares": position}
            cash *= 0.05
        # 超买卖出
        elif rsi > overbought and position > 0:
            pnl = (price - open_trade["entry_price"]) * open_trade["shares"]
            cash = position * price
            trades.append({"entry_date": open_trade["entry_date"], "exit_date": df["date"].iloc[i],
                           "entry_price": open_trade["entry_price"], "exit_price": price, "pnl": pnl})
            position = 0
            open_trade = None

        equity.append(cash + position * price)

    if position > 0 and open_trade:
        last_price = df["close"].iloc[-1]
        pnl = (last_price - open_trade["entry_price"]) * open_trade["shares"]
        trades.append({"entry_date": open_trade["entry_date"], "exit_date": df["date"].iloc[-1],
                       "entry_price": open_trade["entry_price"], "exit_price": last_price, "pnl": pnl})
        equity.append(cash + position * last_price)

    return np.array(equity) if equity else np.array([initial_capital]), trades


def backtest_buy_hold(df: pd.DataFrame, initial_capital: float = 100000) -> Tuple[np.ndarray, List[Dict]]:
    """买入持有基准策略"""
    if df.empty:
        return np.array([initial_capital]), []
    shares = initial_capital / df["close"].iloc[0] * 0.95
    equity = (shares * df["close"].values) + initial_capital * 0.05
    pnl = (df["close"].iloc[-1] - df["close"].iloc[0]) * shares
    return equity, [{"entry_date": str(df["date"].iloc[0]), "exit_date": str(df["date"].iloc[-1]),
                     "entry_price": df["close"].iloc[0], "exit_price": df["close"].iloc[-1], "pnl": pnl}]


STRATEGIES = {
    "MA_5_20": lambda df: backtest_ma_crossover(df, 5, 20),
    "MA_10_30": lambda df: backtest_ma_crossover(df, 10, 30),
    "Momentum_20_5": lambda df: backtest_momentum(df, 20, 5),
    "RSI_14_30_70": lambda df: backtest_rsi_mean_reversion(df, 14, 30, 70),
    "Buy_Hold": lambda df: backtest_buy_hold(df),
}


def main():
    print(f"{'='*120}")
    print(f"批量回测报告 - {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*120}")

    all_results = {}

    for symbol, name in STOCKS.items():
        print(f"\n>>> [{symbol} {name}] 获取K线数据...", end=" ", flush=True)
        df = fetch_kline(symbol, days=365)
        if df.empty:
            print("FAILED")
            continue
        print(f"OK ({len(df)} 根K线, {df['date'].iloc[0].strftime('%Y-%m-%d')} ~ {df['date'].iloc[-1].strftime('%Y-%m-%d')})")

        stock_results = {}
        for strat_name, strat_fn in STRATEGIES.items():
            equity, trades = strat_fn(df)
            metrics = compute_metrics(equity, trades, 100000)
            stock_results[strat_name] = metrics

        all_results[f"{symbol} {name}"] = stock_results

        # 打印单只股票结果
        print(f"  {'策略':<20} {'总收益%':>8} {'年化%':>8} {'最大回撤%':>9} {'夏普':>6} {'胜率%':>7} {'交易数':>6} {'盈亏比':>7}")
        print(f"  {'-'*80}")
        for sn, sm in stock_results.items():
            print(f"  {sn:<20} {sm['total_return']:>7.2f} {sm['annual_return']:>7.2f} "
                  f"{sm['max_drawdown']:>8.2f} {sm['sharpe_ratio']:>5.2f} {sm['win_rate']:>6.1f} "
                  f"{sm['total_trades']:>5d} {sm['profit_factor']:>6.2f}")

    # 综合排名
    print(f"\n{'='*120}")
    print("综合排名 (按MA_5_20策略年化收益排序)")
    print(f"{'='*120}")
    print(f"  {'股票':<22} {'策略':<20} {'总收益%':>8} {'年化%':>8} {'最大回撤%':>9} {'夏普':>6} {'胜率%':>7} {'交易数':>6}")

    ranking = []
    for stock_name, strategies in all_results.items():
        for sn, sm in strategies.items():
            ranking.append((stock_name, sn, sm))

    # 按总收益排序（取每个股票表现最好的策略）
    best_per_stock = {}
    for stock_name, sn, sm in ranking:
        if stock_name not in best_per_stock or sm["total_return"] > best_per_stock[stock_name][1]["total_return"]:
            best_per_stock[stock_name] = (sn, sm)

    sorted_best = sorted(best_per_stock.items(), key=lambda x: x[1][1]["total_return"], reverse=True)

    rank = 1
    for stock_name, (sn, sm) in sorted_best:
        emoji = "🟢" if sm["total_return"] > 10 else ("🟡" if sm["total_return"] > 0 else "🔴")
        print(f"  {emoji} #{rank:<2d} {stock_name:<22} {sn:<20} {sm['total_return']:>7.2f} "
              f"{sm['annual_return']:>7.2f} {sm['max_drawdown']:>8.2f} {sm['sharpe_ratio']:>5.2f} "
              f"{sm['win_rate']:>6.1f} {sm['total_trades']:>5d}")
        rank += 1

    # 保存结果JSON
    output = {
        "report_time": datetime.now().isoformat(),
        "stocks": STOCKS,
        "results": {},
    }
    for stock_name, strategies in all_results.items():
        output["results"][stock_name] = {
            sn: {k: (float(v) if isinstance(v, (np.floating, np.integer)) else v) for k, v in sm.items()}
            for sn, sm in strategies.items()
        }

    json_path = "/root/.openclaw/workspace/mx_data/output/backtest_results.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n结果已保存至: {json_path}")


if __name__ == "__main__":
    main()
