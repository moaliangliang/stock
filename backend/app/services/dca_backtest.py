"""
DCA 定投回测服务 — 纳斯达克 / 标普500 月度定投
支持两种模式：定额 (fixed) + 智能 (smart，基于12月均线估值)
"""
import logging
from datetime import date, datetime, timezone
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# 默认标的 — 跨境ETF
DEFAULT_INDICES = {
    "513100.SH": "纳斯达克100",
    "513500.SH": "标普500",
}

# 智能定投参数
SMART_MA_PERIOD = 12          # 均线周期（月）
SMART_AGGRESSIVENESS = 2.0    # 乘数敏感度
SMART_MIN_MULTIPLIER = 0.5    # 最低投入倍数
SMART_MAX_MULTIPLIER = 2.0    # 最高投入倍数


def fetch_kline_for_backtest(symbol: str, start_date: str, end_date: str) -> List[Dict]:
    """获取日K线数据，按日期升序排列。"""
    from app.utils.sina_client import fetch_kline

    start_fmt = start_date.replace("-", "")
    end_fmt = end_date.replace("-", "")

    data = fetch_kline(symbol, "1d", start_fmt, end_fmt)
    if not data:
        return []

    data.sort(key=lambda x: x["timestamp"])
    return data


def _date_from_kline(k: Dict) -> date:
    ts = k["timestamp"]
    if isinstance(ts, datetime):
        return ts.date()
    return ts


def find_monthly_invest_dates(
    klines: List[Dict],
    start_date: date,
    end_date: date,
) -> List[Tuple[date, float]]:
    """从日K线中提取每月首个交易日及收盘价。"""
    month_groups: Dict[Tuple[int, int], List[Tuple[date, float]]] = {}
    for k in klines:
        d = _date_from_kline(k)
        if d < start_date or d > end_date:
            continue
        key = (d.year, d.month)
        month_groups.setdefault(key, []).append((d, k["close"]))

    result = []
    for key in sorted(month_groups.keys()):
        days = month_groups[key]
        days.sort(key=lambda x: x[0])
        result.append(days[0])

    return result


def compute_sma(prices: List[float], period: int) -> List[Optional[float]]:
    """计算简单移动平均，前 period-1 个位置为 None。"""
    sma: List[Optional[float]] = []
    for i in range(len(prices)):
        if i < period - 1:
            sma.append(None)
        else:
            sma.append(sum(prices[i - period + 1 : i + 1]) / period)
    return sma


def simulate_dca_fixed(
    invest_dates: List[Tuple[date, float]],
    amount: float,
) -> List[Dict]:
    """定额定投：每期固定金额买入。"""
    records = []
    total_shares = 0.0
    total_cost = 0.0

    for d, price in invest_dates:
        if price <= 0:
            continue
        shares = amount / price
        total_shares += shares
        total_cost += amount
        current_value = total_shares * price

        records.append({
            "date": d,
            "price": round(price, 4),
            "invest_amount": amount,
            "shares_bought": round(shares, 4),
            "total_shares": round(total_shares, 4),
            "total_cost": round(total_cost, 2),
            "current_value": round(current_value, 2),
            "profit": round(current_value - total_cost, 2),
            "profit_pct": round((current_value / total_cost - 1) * 100, 2) if total_cost > 0 else 0,
        })

    return records


def simulate_dca_wait(
    invest_dates: List[Tuple[date, float]],
    amount: float,
    klines: List[Dict],
    ma_period: int = 250,
) -> List[Dict]:
    """均线等待定投：价格低于 MA 才买入，否则持币等待。

    每月判断: price < MA → 买入（含累计现金）; price >= MA → 跳过，现金累积。
    资金不会闲置 — 跳过的月份现金保留，等条件触发时一次性投入。
    """
    if not klines:
        return simulate_dca_fixed(invest_dates, amount)

    all_dates = [_date_from_kline(k) for k in klines]
    all_closes = [k["close"] for k in klines]
    sma_vals = compute_sma(all_closes, ma_period)

    date_to_sma: Dict[date, float] = {}
    for i, d in enumerate(all_dates):
        if sma_vals[i] is not None:
            date_to_sma[d] = sma_vals[i]

    records = []
    total_shares = 0.0
    total_cost = 0.0
    cash_reserve = 0.0         # 等待期间累积的现金
    total_cash_saved = 0.0     # 累计预留现金总额

    for d, price in invest_dates:
        if price <= 0:
            continue

        sma_val = _find_nearest_sma(d, date_to_sma)

        # 每月先把基准金额加入现金储备
        cash_reserve += amount
        total_cash_saved += amount

        # 判断是否买入
        should_buy = sma_val is not None and price < sma_val

        if should_buy and cash_reserve > 0:
            invest_amount = round(cash_reserve, 2)
            shares = invest_amount / price
            total_shares += shares
            total_cost += invest_amount
            cash_reserve = 0.0
            action = "buy"
        else:
            invest_amount = 0.0
            shares = 0.0
            action = "wait"

        current_value = total_shares * price + cash_reserve

        records.append({
            "date": d,
            "price": round(price, 4),
            "sma": round(sma_val, 4) if sma_val else None,
            "action": action,
            "invest_amount": invest_amount,
            "shares_bought": round(shares, 4),
            "total_shares": round(total_shares, 4),
            "total_cost": round(total_cost, 2),
            "cash_reserve": round(cash_reserve, 2),
            "current_value": round(current_value, 2),
            "profit": round(current_value - total_cost - cash_reserve, 2),
            "profit_pct": round((current_value / (total_cost + cash_reserve) - 1) * 100, 2) if (total_cost + cash_reserve) > 0 else 0,
        })

    return records


def simulate_dca_smart(
    invest_dates: List[Tuple[date, float]],
    amount: float,
    klines: List[Dict],
    ma_period: int = SMART_MA_PERIOD,
    aggressiveness: float = SMART_AGGRESSIVENESS,
    min_multiplier: float = SMART_MIN_MULTIPLIER,
    max_multiplier: float = SMART_MAX_MULTIPLIER,
) -> List[Dict]:
    """智能定投：根据12月均线估值调整每期投入金额。

    价格低于12月均线 → 低估 → 加大投入（最高2倍）
    价格高于12月均线 → 高估 → 减少投入（最低0.5倍）

    乘数公式: multiplier = 1 + deviation * aggressiveness
      其中 deviation = (SMA - price) / SMA
    """
    if not klines:
        return simulate_dca_fixed(invest_dates, amount)

    # 构建日线价格序列用于计算 SMA
    all_dates = [_date_from_kline(k) for k in klines]
    all_closes = [k["close"] for k in klines]
    sma12 = compute_sma(all_closes, ma_period)

    # 构建 date → sma 映射
    date_to_sma: Dict[date, float] = {}
    for i, d in enumerate(all_dates):
        if sma12[i] is not None:
            date_to_sma[d] = sma12[i]

    records = []
    total_shares = 0.0
    total_cost = 0.0

    for d, price in invest_dates:
        if price <= 0:
            continue

        # 找最近的 SMA 值
        sma_val = _find_nearest_sma(d, date_to_sma)

        if sma_val is not None and sma_val > 0:
            deviation = (sma_val - price) / sma_val
            multiplier = 1.0 + deviation * aggressiveness
            multiplier = max(min_multiplier, min(max_multiplier, multiplier))
        else:
            multiplier = 1.0

        invest_amount = round(amount * multiplier, 2)
        shares = invest_amount / price
        total_shares += shares
        total_cost += invest_amount
        current_value = total_shares * price

        records.append({
            "date": d,
            "price": round(price, 4),
            "sma12": round(sma_val, 4) if sma_val else None,
            "multiplier": round(multiplier, 2),
            "invest_amount": invest_amount,
            "shares_bought": round(shares, 4),
            "total_shares": round(total_shares, 4),
            "total_cost": round(total_cost, 2),
            "current_value": round(current_value, 2),
            "profit": round(current_value - total_cost, 2),
            "profit_pct": round((current_value / total_cost - 1) * 100, 2) if total_cost > 0 else 0,
        })

    return records


def _find_nearest_sma(target_date: date, date_to_sma: Dict[date, float]) -> Optional[float]:
    """找到距目标日期最近的 SMA 值（不超过5天）。"""
    if target_date in date_to_sma:
        return date_to_sma[target_date]

    from datetime import timedelta
    for offset in range(1, 6):
        for candidate in (target_date - timedelta(days=offset), target_date + timedelta(days=offset)):
            if candidate in date_to_sma:
                return date_to_sma[candidate]

    return None


def compute_max_drawdown(values: List[float]) -> float:
    """计算最大回撤（小数形式）。"""
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


def compute_xirr(cashflows: List[float], dates_list: List[date], guess: float = 0.1) -> Optional[float]:
    """Newton-Raphson XIRR 计算年化收益率。返回小数，无解返回 None。"""
    if len(cashflows) < 2:
        return None

    rate = guess
    days = [(d - dates_list[0]).days for d in dates_list]

    for _ in range(100):
        f_val = 0.0
        f_prime = 0.0
        for cf, day in zip(cashflows, days):
            if rate <= -1:
                return None
            denom = (1.0 + rate) ** (day / 365.0)
            if denom == 0:
                return None
            f_val += cf / denom
            f_prime += -cf * (day / 365.0) / (denom * (1.0 + rate))
        if abs(f_val) < 1e-7:
            return rate
        if f_prime == 0:
            return None
        rate -= f_val / f_prime

    return None


def compute_dca_metrics(
    records: List[Dict],
    end_date: date,
    base_amount: float,
) -> Dict:
    """汇总 DCA 指标。"""
    if not records:
        return {}

    last = records[-1]
    total_cost = last["total_cost"]
    cash_reserve = last.get("cash_reserve", 0)
    total_invested = total_cost + cash_reserve  # 已投入 + 手持现金
    final_value = last["current_value"]
    total_return_pct = (final_value / total_invested - 1) * 100 if total_invested > 0 else 0

    # XIRR: 过滤掉 invest_amount==0 的期数（等待模式跳过月份）
    cashflows = []
    cf_dates = []
    for r in records:
        inv = r.get("invest_amount", 0)
        if inv > 0:
            cashflows.append(-inv)
            cf_dates.append(r["date"])
    # 如果最后一期还有现金储备未投入，加到最终价值
    cashflows.append(final_value)
    cf_dates.append(end_date)

    xirr = compute_xirr(cashflows, cf_dates) if len(cashflows) >= 2 else None
    xirr_pct = xirr * 100 if xirr is not None else None

    values = [r["current_value"] for r in records]
    max_dd = compute_max_drawdown(values) * 100

    total_periods = len(records)
    avg_invest = total_invested / total_periods if total_periods > 0 else 0

    # 统计买入次数
    buy_count = sum(1 for r in records if r.get("invest_amount", 0) > 0)

    return {
        "total_invested": round(total_invested, 2),
        "final_value": round(final_value, 2),
        "total_return_pct": round(total_return_pct, 2),
        "annualized_xirr_pct": round(xirr_pct, 2) if xirr_pct is not None else None,
        "max_drawdown_pct": round(max_dd, 2),
        "investment_count": total_periods,
        "buy_count": buy_count,
        "avg_invest_amount": round(avg_invest, 2),
        "last_price": last["price"],
        "cash_reserve": round(cash_reserve, 2),
        "cost_basis": round(total_cost / last["total_shares"], 4) if last.get("total_shares", 0) > 0 else 0,
        "records": records,
    }


def simulate_lumpsum(
    klines: List[Dict],
    total_amount: float,
    start_date: date,
) -> Optional[Dict]:
    """一次性投入并持有，与定投对比。"""
    if not klines:
        return None

    first_price = None
    first_date = None
    last_price = None
    last_date = None

    for k in klines:
        d = _date_from_kline(k)
        if first_date is None and d >= start_date:
            first_date = d
            first_price = k["close"]
        last_date = d
        last_price = k["close"]

    if first_price is None or last_price is None or first_price <= 0:
        return None

    shares = total_amount / first_price
    final_value = shares * last_price
    total_return_pct = (final_value / total_amount - 1) * 100

    # 计算年化收益率
    days_held = (last_date - first_date).days
    if days_held > 0 and total_amount > 0:
        annual_return = ((final_value / total_amount) ** (365.0 / days_held) - 1) * 100
    else:
        annual_return = 0.0

    return {
        "total_invested": round(total_amount, 2),
        "final_value": round(final_value, 2),
        "total_return_pct": round(total_return_pct, 2),
        "annual_return_pct": round(annual_return, 2),
        "buy_price": round(first_price, 4),
        "current_price": round(last_price, 4),
        "buy_date": first_date.isoformat(),
        "end_date": last_date.isoformat(),
        "days_held": days_held,
    }


def run_dca_backtest(
    symbols: List[str],
    start_date: str,
    end_date: str,
    amount: float,
    mode: str = "fixed",
    smart_aggressiveness: float = SMART_AGGRESSIVENESS,
    smart_min_multiplier: float = SMART_MIN_MULTIPLIER,
    smart_max_multiplier: float = SMART_MAX_MULTIPLIER,
) -> Dict:
    """执行 DCA 回测的主入口。

    Args:
        symbols: ETF 代码列表，如 ["513100.SH", "513500.SH"]
        start_date: 起始日期 "YYYY-MM-DD"
        end_date: 结束日期 "YYYY-MM-DD"
        amount: 每期基准定投金额
        mode: "fixed" 或 "smart"
        smart_aggressiveness: 智能模式乘数敏感度
        smart_min_multiplier: 智能模式最低投入倍数
        smart_max_multiplier: 智能模式最高投入倍数

    Returns:
        {results: {symbol: metrics}, comparison: {...}, params: {...}}
    """
    sd = datetime.strptime(start_date, "%Y-%m-%d").date()
    ed = datetime.strptime(end_date, "%Y-%m-%d").date()

    name_map = DEFAULT_INDICES

    results = {}
    lumpsum_results = {}
    errors = []

    for symbol in symbols:
        name = name_map.get(symbol, symbol)

        # 获取 K 线
        klines = fetch_kline_for_backtest(symbol, start_date, end_date)
        if not klines or len(klines) < 12:
            errors.append(f"{name} ({symbol}): 数据不足")
            continue

        # 找每月投资日
        invest_dates = find_monthly_invest_dates(klines, sd, ed)
        if len(invest_dates) < 2:
            errors.append(f"{name} ({symbol}): 投资期数不足")
            continue

        # 执行 DCA 模拟
        if mode == "smart":
            records = simulate_dca_smart(
                invest_dates, amount, klines,
                aggressiveness=smart_aggressiveness,
                min_multiplier=smart_min_multiplier,
                max_multiplier=smart_max_multiplier,
            )
        elif mode == "wait":
            records = simulate_dca_wait(invest_dates, amount, klines)
        else:
            records = simulate_dca_fixed(invest_dates, amount)

        metrics = compute_dca_metrics(records, ed, amount)
        metrics["name"] = name
        metrics["symbol"] = symbol
        results[symbol] = metrics

        # 一次性投入对比
        total_invested = metrics["total_invested"]
        ls = simulate_lumpsum(klines, total_invested, sd)
        if ls:
            ls["name"] = name
            lumpsum_results[symbol] = ls

    if not results:
        return {"error": "所有标的回测失败", "details": errors}

    # 等权组合汇总
    port_total_invested = sum(m["total_invested"] for m in results.values())
    port_total_value = sum(m["final_value"] for m in results.values())

    # 组合月度市值
    monthly_portfolio: Dict[Tuple[int, int], float] = {}
    for symbol in results:
        for r in results[symbol]["records"]:
            ym = (r["date"].year, r["date"].month)
            monthly_portfolio[ym] = monthly_portfolio.get(ym, 0) + r["current_value"]

    port_values = [monthly_portfolio[ym] for ym in sorted(monthly_portfolio)]
    port_max_dd = compute_max_drawdown(port_values) * 100

    # 组合 XIRR
    all_cf = []
    all_dates = []
    for symbol in results:
        for r in results[symbol]["records"]:
            all_cf.append(-r["invest_amount"])
            all_dates.append(r["date"])
    all_cf.append(port_total_value)
    all_dates.append(ed)
    port_xirr = compute_xirr(all_cf, all_dates)

    # 月度序列（用于前端图表）
    all_yms = sorted(set(
        (r["date"].year, r["date"].month)
        for m in results.values()
        for r in m["records"]
    ))
    monthly_series = []
    for ym in all_yms:
        entry = {"year": ym[0], "month": ym[1], "label": f"{ym[0]:04d}-{ym[1]:02d}"}
        for symbol in symbols:
            if symbol in results:
                for r in results[symbol]["records"]:
                    if (r["date"].year, r["date"].month) == ym:
                        entry[symbol] = round(r["current_value"], 2)
                        break
                if symbol not in entry:
                    entry[symbol] = None
        portfolio_val = sum(
            entry.get(s, 0) or 0 for s in symbols if s in results
        )
        entry["portfolio"] = round(portfolio_val, 2)
        monthly_series.append(entry)

    return {
        "results": results,
        "lumpsum": lumpsum_results,
        "comparison": {
            "total_invested": round(port_total_invested, 2),
            "total_value": round(port_total_value, 2),
            "total_profit": round(port_total_value - port_total_invested, 2),
            "total_return_pct": round((port_total_value / port_total_invested - 1) * 100, 2) if port_total_invested > 0 else 0,
            "annualized_xirr_pct": round(port_xirr * 100, 2) if port_xirr is not None else None,
            "max_drawdown_pct": round(port_max_dd, 2),
        },
        "monthly_series": monthly_series,
        "params": {
            "symbols": symbols,
            "start_date": start_date,
            "end_date": end_date,
            "amount": amount,
            "mode": mode,
            "smart_aggressiveness": smart_aggressiveness if mode == "smart" else None,
        },
        "errors": errors if errors else None,
    }
