#!/usr/bin/env python3
"""
DCA 定投回测 — 沪深300(510300) / 中证500(510500) / 创业板指(159915)
按月固定金额定投，近5年数据对比。
"""
import argparse
import logging
import sys
import os
from datetime import datetime, date, timedelta, timezone

logging.disable(logging.CRITICAL)
os.environ.setdefault("DEBUG", "true")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.utils.sina_client import fetch_kline

# ============================================================
# 默认参数
# ============================================================
DEFAULT_ETFS = [
    ("510300.SH", "沪深300"),
    ("510500.SH", "中证500"),
    ("159915.SZ", "创业板指"),
]
DEFAULT_AMOUNT = 1000.0
DEFAULT_START = "2021-01-01"
DEFAULT_END = datetime.now().strftime("%Y-%m-%d")


# ============================================================
# 数据获取
# ============================================================

def fetch_multi_year_klines(symbol: str, start_date: str, end_date: str) -> list:
    """获取日K线数据。"""
    data = fetch_kline(symbol, "1d", start_date, end_date)
    if not data:
        return []
    data.sort(key=lambda x: x["timestamp"])
    return data


# ============================================================
# 定投核心逻辑
# ============================================================

def find_monthly_invest_dates(klines: list, start_date: date, end_date: date, day_of_month: int = 1) -> list:
    """从日K线中提取每月第1个交易日。

    返回 [(price_date, close_price), ...]
    """
    invest_dates = []

    month_groups = {}
    for k in klines:
        ts = k["timestamp"]
        if isinstance(ts, datetime):
            d = ts.date()
        else:
            d = ts
        if d < start_date or d > end_date:
            continue
        key = (d.year, d.month)
        if key not in month_groups:
            month_groups[key] = []
        month_groups[key].append((d, k["close"]))

    for (year, month) in sorted(month_groups.keys()):
        days = month_groups[(year, month)]
        days.sort(key=lambda x: x[1])
        _, price = days[0]
        invest_dates.append((days[0][0], price))

    return invest_dates


def simulate_dca(invest_dates: list, amount: float) -> list:
    """模拟每月定投。返回 records 列表。"""
    records = []
    total_shares = 0.0
    total_cost = 0.0

    for d, price in invest_dates:
        if price <= 0:
            continue
        shares_bought = amount / price
        total_shares += shares_bought
        total_cost += amount
        current_value = total_shares * price

        records.append({
            "date": d,
            "price": price,
            "shares_bought": shares_bought,
            "total_shares": total_shares,
            "total_cost": total_cost,
            "current_value": current_value,
            "profit": current_value - total_cost,
            "profit_pct": (current_value / total_cost - 1) * 100,
        })

    return records


# ============================================================
# 指标计算
# ============================================================

def compute_max_drawdown(values: list) -> float:
    """返回最大回撤（小数）。"""
    if not values:
        return 0.0
    peak = values[0]
    max_dd = 0.0
    for v in values:
        if v > peak:
            peak = v
        if peak > 0:
            dd = (peak - v) / peak
            if dd > max_dd:
                max_dd = dd
    return max_dd


def compute_xirr(cashflows: list, dates_list: list, guess: float = 0.1):
    """Newton-Raphson 计算 XIRR 年化收益率。返回小数，无解返回 None。"""
    if len(cashflows) < 2:
        return None

    rate = guess
    days = [(d - dates_list[0]).days for d in dates_list]

    for _ in range(100):
        f = 0.0
        f_prime = 0.0
        for cf, day in zip(cashflows, days):
            if rate <= -1:
                return None
            denom = (1.0 + rate) ** (day / 365.0)
            if denom == 0:
                return None
            f += cf / denom
            f_prime += -cf * (day / 365.0) / (denom * (1.0 + rate))

        if abs(f) < 1e-7:
            return rate
        if f_prime == 0:
            return None
        rate -= f / f_prime

    return None


def compute_metrics(records: list, final_date: date, amount: float) -> dict:
    """汇总定投指标。"""
    if not records:
        return {}

    last = records[-1]
    total_invested = last["total_cost"]
    final_value = last["current_value"]
    total_return_pct = (final_value / total_invested - 1) * 100 if total_invested > 0 else 0

    # XIRR
    cashflows = []
    cf_dates = []
    for r in records:
        cashflows.append(-amount)
        cf_dates.append(r["date"])
    cashflows.append(final_value)
    cf_dates.append(final_date)

    xirr = compute_xirr(cashflows, cf_dates)
    xirr_pct = xirr * 100 if xirr is not None else None

    values = [r["current_value"] for r in records]
    max_dd = compute_max_drawdown(values) * 100

    return {
        "total_invested": total_invested,
        "final_value": final_value,
        "total_return_pct": total_return_pct,
        "annualized_xirr_pct": xirr_pct,
        "max_drawdown_pct": max_dd,
        "investment_count": len(records),
        "last_price": last["price"],
        "cost_basis": total_invested / last["total_shares"] if last["total_shares"] > 0 else 0,
        "records": records,
    }


# ============================================================
# 一次性投入对比
# ============================================================

def simulate_lumpsum(klines: list, total_amount: float, invest_date: date, end_date: date = None) -> dict:
    """模拟一次性投入并持有。"""
    first = None
    last = None

    for k in klines:
        ts = k["timestamp"]
        d = ts.date() if isinstance(ts, datetime) else ts
        if end_date and d > end_date:
            break
        if first is None and d >= invest_date:
            first = (d, k["close"])
        last = (d, k["close"])

    if not first or not last:
        return None

    shares = total_amount / first[1] if first[1] > 0 else 0
    final_value = shares * last[1]
    total_return_pct = (final_value / total_amount - 1) * 100

    return {
        "total_invested": total_amount,
        "final_value": final_value,
        "total_return_pct": total_return_pct,
        "buy_price": first[1],
        "current_price": last[1],
        "buy_date": first[0],
        "end_date": last[0],
    }


# ============================================================
# 输出
# ============================================================

def print_report(dca_results: dict, lumpsum_results: dict, etf_list: list,
                 start_date: date, end_date: date, amount: float):
    """打印完整回测报告。"""
    sep = "=" * 78
    dash = "─" * 78

    print(f"\n{sep}")
    print(f"  DCA 定投回测报告")
    print(f"  数据区间: {start_date} ~ {end_date}")
    print(f"  每期定投额: {amount:,.2f} CNY")
    print(f"  定投日: 每月首交易日")
    print(f"  数据源: 新浪财经")
    print(f"{sep}")

    # --- 一、各ETF对比 ---
    print(f"\n一、各 ETF 定投对比")
    print(dash)
    header = (f"  {'ETF':<22} {'期数':>4} {'总投入(CNY)':>14} {'市值(CNY)':>14} "
              f"{'收益(CNY)':>12} {'收益率%':>9} {'年化%':>9} {'最大回撤%':>8}")
    print(header)
    print(dash)

    for symbol, name in etf_list:
        m = dca_results[symbol]
        profit = m["final_value"] - m["total_invested"]
        xirr_str = (f"{m['annualized_xirr_pct']:+.2f}%"
                     if m['annualized_xirr_pct'] is not None else "     N/A")

        print(f"  {name:<8} ({symbol:<8}) "
              f"{m['investment_count']:>4} "
              f"{m['total_invested']:>14,.2f} "
              f"{m['final_value']:>14,.2f} "
              f"{profit:>+12,.2f} "
              f"{m['total_return_pct']:>+8.2f}% "
              f"{xirr_str:>10} "
              f"{m['max_drawdown_pct']:>7.2f}%")
    print(dash)

    # --- 二、组合汇总 ---
    print(f"\n二、组合汇总 (等权定投)")
    print(dash)

    total_invested = sum(m["total_invested"] for m in dca_results.values())
    total_value = sum(m["final_value"] for m in dca_results.values())
    total_profit = total_value - total_invested
    total_return_pct = (total_value / total_invested - 1) * 100 if total_invested > 0 else 0

    # 组合 XIRR：合并所有现金流
    all_cf = []
    all_dates = []
    for symbol in dca_results:
        m = dca_results[symbol]
        for r in m["records"]:
            all_cf.append(-amount)
            all_dates.append(r["date"])
    all_cf.append(total_value)
    all_dates.append(end_date)

    port_xirr = compute_xirr(all_cf, all_dates)
    port_xirr_str = f"{port_xirr * 100:+.2f}%" if port_xirr is not None else "     N/A"

    # 组合最大回撤
    monthly_portfolio = {}
    for symbol in dca_results:
        m = dca_results[symbol]
        for r in m["records"]:
            ym = (r["date"].year, r["date"].month)
            monthly_portfolio.setdefault(ym, 0)
            monthly_portfolio[ym] += r["current_value"]

    port_values = list(monthly_portfolio.values())
    port_max_dd = compute_max_drawdown(port_values) * 100

    print(f"  总投入:              {total_invested:>14,.2f} CNY")
    print(f"  组合当前市值:        {total_value:>14,.2f} CNY")
    print(f"  总收益:              {total_profit:>+14,.2f} CNY ({total_return_pct:>+.2f}%)")
    print(f"  年化收益率 (XIRR):   {port_xirr_str:>14}")
    print(f"  最大回撤:            {port_max_dd:>13.2f}%")
    print(f"  定投期数:            {next(iter(dca_results.values()))['investment_count']:>14} 个月")
    print(f"  最新日期:            {end_date.isoformat():>14}")
    print(dash)

    # --- 三、月度市值走势 ---
    print(f"\n三、各 ETF 月度市值走势 (单位: CNY)")
    print(dash)

    all_yms = set()
    monthly_data = {}
    for symbol in dca_results:
        m = dca_results[symbol]
        for r in m["records"]:
            ym = (r["date"].year, r["date"].month)
            all_yms.add(ym)
            monthly_data[(symbol, ym)] = r["current_value"]

    print(f"  {'日期':<10}", end="")
    for _, name in etf_list:
        print(f"  {name:>12}", end="")
    print(f"  {'组合合计':>12}")

    for ym in sorted(all_yms):
        print(f"  {ym[0]:04d}-{ym[1]:02d}", end="")
        for symbol, _ in etf_list:
            val = monthly_data.get((symbol, ym), 0)
            print(f"  {val:>12,.2f}", end="")
        port_val = sum(monthly_data.get((symbol, ym), 0) for symbol, _ in etf_list)
        print(f"  {port_val:>12,.2f}")

    print(dash)

    # --- 四、定投 vs 一次性投入 ---
    print(f"\n四、定投 vs 一次性投入对比")
    print(dash)
    print(f"  {'方式':<16} {'总投入':>14} {'当前市值':>14} {'总收益':>14} {'收益率%':>9}")
    print(dash)

    lumpsum_total_value = sum(ls["final_value"] for ls in lumpsum_results.values())
    lumpsum_total_cost = sum(ls["total_invested"] for ls in lumpsum_results.values())

    if lumpsum_total_cost > 0:
        ls_return_pct = (lumpsum_total_value / lumpsum_total_cost - 1) * 100
        print(f"  {'定投':<16} {total_invested:>14,.2f} {total_value:>14,.2f} "
              f"{total_profit:>+14,.2f} {total_return_pct:>+8.2f}%")
        print(f"  {'一次性投入':<16} {lumpsum_total_cost:>14,.2f} {lumpsum_total_value:>14,.2f} "
              f"{lumpsum_total_value - lumpsum_total_cost:>+14,.2f} {ls_return_pct:>+8.2f}%")
    print(dash)
    print(f"  (一次性投入: 在起始日期按收盘价等额买入所有ETF并持有至今)")
    print(dash)

    # --- 五、各ETF详细月度明细 ---
    print(f"\n五、各 ETF 定投明细 (每半年末)")
    print(dash)

    for symbol, name in etf_list:
        m = dca_results[symbol]
        print(f"\n  {name} ({symbol}):")
        print(f"  {'日期':<12} {'本期投入':>10} {'累计投入':>12} {'累计份额':>12} {'市值':>12} {'收益':>12} {'收益率':>8}")
        print(f"  {'─'*68}")
        for i, r in enumerate(m["records"]):
            if r["date"].month % 6 != 0 and i != len(m["records"]) - 1:
                continue
            print(f"  {r['date'].isoformat():<12} "
                  f"{amount:>10,.2f} "
                  f"{r['total_cost']:>12,.2f} "
                  f"{r['total_shares']:>12,.2f} "
                  f"{r['current_value']:>12,.2f} "
                  f"{r['profit']:>+12,.2f} "
                  f"{r['profit_pct']:>+7.2f}%")

    print(f"\n{sep}")
    print(f"  报告生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{sep}\n")


# ============================================================
# 主函数
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="ETF 定投回测")
    parser.add_argument("--amount", type=float, default=DEFAULT_AMOUNT, help="每期定投金额")
    parser.add_argument("--day", type=int, default=1, help="每月定投日")
    parser.add_argument("--start", type=str, default=DEFAULT_START, help="起始日期 YYYY-MM-DD")
    parser.add_argument("--end", type=str, default=DEFAULT_END, help="结束日期 YYYY-MM-DD")
    parser.add_argument("--symbols", type=str, default=",".join(s for s, _ in DEFAULT_ETFS),
                        help="ETF代码列表，逗号分隔")
    args = parser.parse_args()

    start_date = datetime.strptime(args.start, "%Y-%m-%d").date()
    end_date = datetime.strptime(args.end, "%Y-%m-%d").date()

    symbols = args.symbols.split(",") if args.symbols else [s for s, _ in DEFAULT_ETFS]
    etf_list = []
    name_map = {s: n for s, n in DEFAULT_ETFS}
    for s in symbols:
        etf_list.append((s, name_map.get(s, s)))

    dca_results = {}
    lumpsum_results = {}

    for symbol, name in etf_list:
        print(f"\n  正在获取 {name} ({symbol}) 历史K线数据...", end="", flush=True)
        klines = fetch_multi_year_klines(symbol, args.start.replace("-", ""), args.end.replace("-", ""))
        if not klines or len(klines) < 20:
            print(f" ❌ 数据不足")
            continue
        print(f" {len(klines)} 条日K线")

        print(f"  正在模拟定投 {name}...", end="", flush=True)
        invest_dates = find_monthly_invest_dates(klines, start_date, end_date, args.day)
        records = simulate_dca(invest_dates, args.amount)
        metrics = compute_metrics(records, end_date, args.amount)
        dca_results[symbol] = metrics
        print(f" {len(records)} 期")

        # 一次性投入（与定投总金额相同）
        lumpsum_amount = args.amount * len(records)
        ls = simulate_lumpsum(klines, lumpsum_amount, start_date, end_date)
        if ls:
            lumpsum_results[symbol] = ls

    if not dca_results:
        print("\n❌ 没有成功获取到任何数据，请检查网络或ETF代码。")
        sys.exit(1)

    valid_etf_list = [(s, n) for s, n in etf_list if s in dca_results]

    print_report(dca_results, lumpsum_results, valid_etf_list,
                 start_date, end_date, args.amount)


if __name__ == "__main__":
    main()
