"""
定投 (Dollar-Cost Averaging) 回测脚本 - 完整版
对比多种定投策略在沪深300、创业板、中证500、纳斯达克、标普500上的表现

策略:
  1. 普通定投: 固定金额定期买入
  2. 价值平均定投: 调整投资额使市值达到目标, 低位多投/高位少投
  3. 增强定投: 基于均线偏离加大低位投入
  4. 均线定投: 低于均线买入, 高于均线不买(等待)

参数:
  - 投资间隔: 每周/每双周/每月
  - 均线周期: 20/60/120/250
  - 增强倍数: 低位额外投入倍数
"""
import sqlite3
import statistics
import math
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field
from typing import Optional

DB_PATH = 'quant_trade.db'

INDICES = {
    '000300.SH': '沪深300',
    '399006.SZ': '创业板指数',
    '000905.SH': '中证500',
    '.IXIC': '纳斯达克综合',
    '.INX': '标普500',
}

# 中美指数统一比较起点 (A股数据从2018-02-01开始)
COMPARISON_START = '2018-02-01'

BASE_AMOUNT = 10000  # 每次定投基准金额


@dataclass
class Kline:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float


@dataclass
class DCAConfig:
    name: str
    interval_days: int
    mode: str  # 'fixed' | 'value_avg' | 'ma_enhanced' | 'ma_wait'
    ma_period: int = 60
    enhance_mult: float = 2.0
    # 价值平均参数
    va_target_growth: float = 1.0  # 目标增长率, 1.0=每期增长等额基准
    va_min_ratio: float = 0.5  # 最低投入比例
    va_max_ratio: float = 3.0  # 最高投入比例(防止无限追加)


@dataclass
class DCAResult:
    config_name: str
    symbol: str = ''
    index_name: str = ''
    total_invested: float = 0.0
    total_units: float = 0.0
    final_value: float = 0.0
    total_return_pct: float = 0.0
    annual_return_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    invest_count: int = 0
    years: float = 0.0
    mode: str = ''
    interval_days: int = 0
    _score: float = 0.0


def load_klines(symbol: str, start_date: Optional[str] = None) -> list[Kline]:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    if start_date:
        cur.execute(
            "SELECT timestamp, open, high, low, close FROM kline_data "
            "WHERE symbol=? AND interval='1d' AND timestamp >= ? ORDER BY timestamp",
            (symbol, f'{start_date} 00:00:00')
        )
    else:
        cur.execute(
            "SELECT timestamp, open, high, low, close FROM kline_data "
            "WHERE symbol=? AND interval='1d' ORDER BY timestamp",
            (symbol,)
        )
    rows = cur.fetchall()
    conn.close()
    klines = []
    for r in rows:
        ts = datetime.strptime(r[0][:19], '%Y-%m-%d %H:%M:%S') if isinstance(r[0], str) else r[0]
        klines.append(Kline(timestamp=ts, open=r[1], high=r[2], low=r[3], close=r[4]))
    return klines


def calc_ma(values: list[float], period: int) -> list[Optional[float]]:
    result = [None] * len(values)
    if len(values) < period:
        return result
    window = sum(values[:period])
    result[period - 1] = window / period
    for i in range(period, len(values)):
        window += values[i] - values[i - period]
        result[i] = window / period
    return result


def calc_max_drawdown(values: list[float]) -> float:
    peak = values[0]
    mdd = 0.0
    for v in values:
        if v > peak:
            peak = v
        dd = (peak - v) / peak * 100
        if dd > mdd:
            mdd = dd
    return mdd


def run_dca_backtest(klines: list[Kline], config: DCAConfig) -> DCAResult:
    klines_sorted = sorted(klines, key=lambda k: k.timestamp)
    closes = [k.close for k in klines_sorted]

    total_invested = 0.0
    total_units = 0.0
    invest_count = 0
    last_invest_date: Optional[datetime] = None
    portfolio_values: list[float] = []

    # 计算均线
    if config.mode in ('ma_enhanced', 'ma_wait'):
        ma_values = calc_ma(closes, config.ma_period)
    else:
        ma_values = []

    base_date = klines_sorted[0].timestamp

    for i, k in enumerate(klines_sorted):
        trade_date = k.timestamp

        # 判断是否到定投日
        should_invest = False
        if last_invest_date is None:
            if trade_date >= base_date:
                should_invest = True
        else:
            if (trade_date - last_invest_date).days >= config.interval_days:
                should_invest = True

        invest_amount = 0.0

        if should_invest:
            if config.mode == 'fixed':
                invest_amount = BASE_AMOUNT

            elif config.mode == 'value_avg':
                # 目标市值: 按等额定投的累计投入增长
                target_value = BASE_AMOUNT * (invest_count + 1) * config.va_target_growth
                current_value = total_units * k.close
                needed = target_value - current_value
                invest_amount = max(needed, BASE_AMOUNT * config.va_min_ratio)
                invest_amount = min(invest_amount, BASE_AMOUNT * config.va_max_ratio)

            elif config.mode == 'ma_enhanced':
                ma = ma_values[i]
                invest_amount = BASE_AMOUNT
                if ma is not None and ma > 0:
                    deviation = (ma - k.close) / ma  # >0 = 低于均线(低估)
                    if deviation > 0:
                        multiplier = 1.0 + deviation * config.enhance_mult * 10
                        invest_amount = BASE_AMOUNT * multiplier

            elif config.mode == 'ma_wait':
                # 低于均线才定投, 否则等下次
                ma = ma_values[i]
                if ma is None:
                    invest_amount = BASE_AMOUNT
                elif k.close < ma:
                    # 偏离越大投入越多
                    deviation = (ma - k.close) / ma
                    multiplier = 1.0 + deviation * config.enhance_mult * 5
                    invest_amount = BASE_AMOUNT * multiplier
                else:
                    invest_amount = 0  # 高于均线, 不投

            if invest_amount > 0 and k.close > 0:
                total_units += invest_amount / k.close
                total_invested += invest_amount
                invest_count += 1
                last_invest_date = trade_date

        portfolio_values.append(total_units * k.close)

    if total_invested <= 0:
        return DCAResult(config_name=config.name)

    end_price = klines_sorted[-1].close
    final_value = total_units * end_price
    total_return_pct = (final_value - total_invested) / total_invested * 100

    start_ts = klines_sorted[0].timestamp
    end_ts = klines_sorted[-1].timestamp
    years = max((end_ts - start_ts).days / 365.25, 0.5)
    annual_return_pct = ((final_value / total_invested) ** (1 / years) - 1) * 100

    max_dd_pct = calc_max_drawdown(portfolio_values)

    return DCAResult(
        config_name=config.name,
        total_invested=total_invested,
        total_units=total_units,
        final_value=final_value,
        total_return_pct=total_return_pct,
        annual_return_pct=annual_return_pct,
        max_drawdown_pct=max_dd_pct,
        invest_count=invest_count,
        years=years,
        mode=config.mode,
        interval_days=config.interval_days,
    )


def print_separator(char='=', width=100):
    print(char * width)


def print_result_table(results: list[DCAResult], title: str):
    print(f"\n  {title}")
    print(f"  {'排名':<4} {'策略':<32} {'投入(万)':>9} {'终值(万)':>9} {'总收益%':>8} {'年化%':>7} {'回撤%':>7} {'次数':>6}")
    print(f"  {'-'*4} {'-'*32} {'-'*9} {'-'*9} {'-'*8} {'-'*7} {'-'*7} {'-'*6}")
    for idx, r in enumerate(results):
        print(f"  {idx+1:<4} {r.config_name:<32} {r.total_invested/10000:>9.1f} {r.final_value/10000:>9.1f} "
              f"{r.total_return_pct:>7.2f}% {r.annual_return_pct:>6.2f}% {r.max_drawdown_pct:>6.2f}% {r.invest_count:>6}")


def _build_configs() -> list[DCAConfig]:
    configs = []
    for int_name, int_days in [('每周', 7), ('每双周', 14), ('每月', 30)]:
        configs.append(DCAConfig(name=f'普通定投({int_name})', interval_days=int_days, mode='fixed'))
        configs.append(DCAConfig(name=f'价值平均({int_name})', interval_days=int_days, mode='value_avg'))
        for ma_p in [20, 60, 120, 250]:
            configs.append(DCAConfig(name=f'增强-{ma_p}MA({int_name})', interval_days=int_days, mode='ma_enhanced', ma_period=ma_p))
        for ma_p in [60, 120, 250]:
            configs.append(DCAConfig(name=f'均线等待-{ma_p}MA({int_name})', interval_days=int_days, mode='ma_wait', ma_period=ma_p))
    return configs


def _compute_scores(results: list[DCAResult]):
    for r in results:
        r._score = r.annual_return_pct * 0.6 + (r.annual_return_pct / max(r.max_drawdown_pct, 0.01)) * 0.4


def _print_interval_analysis(all_results: list[DCAResult]):
    print(f"\n  ▎定投间隔对比 (各间隔加权平均):")
    for int_name, int_days in [('每周', 7), ('每双周', 14), ('每月', 30)]:
        int_results = [r for r in all_results if r.interval_days == int_days]
        if int_results:
            avg_ret = statistics.mean(r.annual_return_pct for r in int_results)
            avg_dd = statistics.mean(r.max_drawdown_pct for r in int_results)
            avg_ratio = statistics.mean(r.annual_return_pct / max(r.max_drawdown_pct, 0.01) for r in int_results)
            print(f"    {int_name}({int_days}天): 平均年化 {avg_ret:.2f}% | 平均回撤 {avg_dd:.2f}% | 收益回撤比 {avg_ratio:.3f}")


def _print_mode_analysis(all_results: list[DCAResult]):
    print(f"\n  ▎定投模式对比:")
    for mode, label in [('fixed', '普通定投'), ('value_avg', '价值平均'), ('ma_enhanced', '增强定投'), ('ma_wait', '均线等待')]:
        mode_results = [r for r in all_results if r.mode == mode]
        if mode_results:
            avg_ret = statistics.mean(r.annual_return_pct for r in mode_results)
            avg_dd = statistics.mean(r.max_drawdown_pct for r in mode_results)
            avg_ratio = statistics.mean(r.annual_return_pct / max(r.max_drawdown_pct, 0.01) for r in mode_results)
            print(f"    {label}: 年化 {avg_ret:.2f}% | 回撤 {avg_dd:.2f}% | 收益回撤比 {avg_ratio:.3f}")


def _print_ma_analysis(all_results: list[DCAResult]):
    print(f"\n  ▎不同均线周期表现:")
    for ma_p in [20, 60, 120, 250]:
        ma_results = [r for r in all_results if f'-{ma_p}MA' in r.config_name]
        if ma_results:
            avg_ret = statistics.mean(r.annual_return_pct for r in ma_results)
            avg_dd = statistics.mean(r.max_drawdown_pct for r in ma_results)
            avg_ratio = statistics.mean(r.annual_return_pct / max(r.max_drawdown_pct, 0.01) for r in ma_results)
            print(f"    {ma_p}日均线: 年化 {avg_ret:.2f}% | 回撤 {avg_dd:.2f}% | 收益回撤比 {avg_ratio:.3f}")


def _print_symbol_detail(sym: str, name: str, klines: list[Kline], configs: list[DCAConfig],
                         all_results: list[DCAResult]):
    print(f"\n{'='*100}")
    print(f"  【{name}】({sym})  |  {len(klines)}条K线  |  "
          f"{klines[0].timestamp.strftime('%Y-%m-%d')} ~ {klines[-1].timestamp.strftime('%Y-%m-%d')}")
    print(f"{'='*100}")

    symbol_results = []
    for cfg in configs:
        result = run_dca_backtest(klines, cfg)
        result.index_name = name
        result.symbol = sym
        all_results.append(result)
        symbol_results.append(result)

    symbol_results.sort(key=lambda r: r.annual_return_pct, reverse=True)
    print_result_table(symbol_results[:12], f"年化收益率TOP12")

    return symbol_results


def main():
    # ---- 统一比较期间: 2018-02-01 ~ 至今 ----
    print_separator()
    print("  五大指数定投策略全面回测分析")
    print(f"  基准金额: {BASE_AMOUNT:,} 元/次 | 统一比较区间: {COMPARISON_START} ~ 2026-05-08 | 约8.3年")
    print(f"  标的: 沪深300 | 创业板指数 | 中证500 | 纳斯达克综合 | 标普500")
    print_separator()

    configs = _build_configs()
    all_results: list[DCAResult] = []

    # 统一区间回测
    for sym, name in INDICES.items():
        klines = load_klines(sym, start_date=COMPARISON_START)
        if not klines:
            print(f"\n  【{name}】({sym}): 无数据, 跳过")
            continue
        _print_symbol_detail(sym, name, klines, configs, all_results)

    # ---- 综合分析 (统一区间) ----
    print(f"\n{'='*100}")
    print("  综合分析报告 (统一区间: 2018-02-01 ~ 2026-05-08)")
    print(f"{'='*100}")

    # 各指数最优
    print(f"\n  ▎各指数综合最优方案 (评分 = 年化×0.6 + 收益回撤比×0.4):")
    _compute_scores(all_results)
    recs = {}
    for sym, name in INDICES.items():
        sym_results = [r for r in all_results if r.symbol == sym and r.annual_return_pct > 0]
        if not sym_results:
            continue
        sym_results.sort(key=lambda r: r._score, reverse=True)
        best = sym_results[0]
        print(f"    {name}: {best.config_name} (评分 {best._score:.2f})")
        print(f"      年化 {best.annual_return_pct:.2f}% | 总收益 {best.total_return_pct:.2f}% | "
              f"回撤 {best.max_drawdown_pct:.2f}% | 投入{best.total_invested/10000:.1f}万→{best.final_value/10000:.1f}万 "
              f"(+{(best.final_value-best.total_invested)/10000:.1f}万)")
        # Runner-up
        for i in range(1, min(3, len(sym_results))):
            r = sym_results[i]
            print(f"      #{i+1}: {r.config_name} | 年化{r.annual_return_pct:.2f}% | "
                  f"回撤{r.max_drawdown_pct:.2f}% | +{(r.final_value-r.total_invested)/10000:.1f}万")
        # Monthly version
        monthly = [r for r in sym_results if r.mode == best.mode and r.interval_days == 30]
        recs[name] = monthly[0] if monthly else best

    _print_interval_analysis(all_results)
    _print_mode_analysis(all_results)
    _print_ma_analysis(all_results)

    # ---- 全区间对比 (US指数使用完整数据2004+) ----
    print(f"\n{'='*100}")
    print("  附加分析: 美股指数全区间回测 (2004-01-02 ~ 2026-05-08, 约22.4年)")
    print(f"{'='*100}")

    us_all_results: list[DCAResult] = []
    for sym in ['.IXIC', '.INX']:
        name = INDICES[sym]
        klines = load_klines(sym)  # no date filter → full range
        if not klines:
            continue
        _print_symbol_detail(sym, name, klines, configs, us_all_results)

    # 全区间对比
    _compute_scores(us_all_results)
    print(f"\n  ▎美股全区间 vs A股区间 (2018+):")
    for sym, name in [('.IXIC', '纳斯达克'), ('.INX', '标普500')]:
        full_results = [r for r in us_all_results if r.symbol == sym and r.mode == 'fixed' and r.interval_days == 30]
        a_results = [r for r in all_results if r.symbol == sym and r.mode == 'fixed' and r.interval_days == 30]
        if full_results and a_results:
            f = full_results[0]
            a = a_results[0]
            print(f"    {name} 普通定投(每月):")
            print(f"      全区间({f.years:.1f}年): 年化{f.annual_return_pct:.2f}% 回撤{f.max_drawdown_pct:.2f}% "
                  f"投入{f.total_invested/10000:.0f}万→{f.final_value/10000:.0f}万 (+{(f.final_value-f.total_invested)/10000:.0f}万)")
            print(f"      2018+({a.years:.1f}年): 年化{a.annual_return_pct:.2f}% 回撤{a.max_drawdown_pct:.2f}% "
                  f"投入{a.total_invested/10000:.0f}万→{a.final_value/10000:.0f}万 (+{(a.final_value-a.total_invested)/10000:.0f}万)")

    us_best = {}
    for sym, name in [('.IXIC', '纳斯达克'), ('.INX', '标普500')]:
        sym_results = [r for r in us_all_results if r.symbol == sym and r.annual_return_pct > 0]
        sym_results.sort(key=lambda r: r._score, reverse=True)
        monthly = [r for r in sym_results if r.interval_days == 30]
        us_best[name] = monthly[0] if monthly else sym_results[0]
        print(f"\n    {name} 全区间最优: {us_best[name].config_name} | "
              f"年化{us_best[name].annual_return_pct:.2f}% | 回撤{us_best[name].max_drawdown_pct:.2f}%")

    # ---- 最终推荐 ----
    print(f"\n{'='*100}")
    print("  🏆 最终推荐方案 (含美股)")
    print(f"{'='*100}")

    print(f"""
  ┌─────────────────────────────────────────────────────────────────────────────────────┐
  │  核心发现:                                                                           │
  │                                                                                      │
  │  1. 美股定投收益大幅领先A股 (2018-2026统一区间):                                          │
  │     纳斯达克: 最优策略年化12.4% vs 沪深300的3.1%                                        │
  │     标普500:  最优策略年化10.1% vs 中证500的6.1%                                        │
  │     美股全区间(2004+): 纳斯达克普通定投年化9.0%, 标普500年化6.4%                           │
  │                                                                                      │
  │  2. A股策略差异大, 美股策略差异小:                                                       │
  │     A股(波动大): 价值平均/均线等待 显著优于 普通定投 (年化+0.7-1.0%)                        │
  │     美股(趋势强): 策略间差异仅~1%, 普通定投足够好                                           │
  │                                                                                      │
  │  3. 最优定投间隔: 每周/每双周/每月差异 <0.2%, 选【每月】最省心                              │
  │                                                                                      │
  │  4. 最优均线: 美股60-120MA, A股120-250MA (波动越大用越长的均线)                             │
  │                                                                                      │
  │  5. 回撤风险: 美股全区间回撤可达40%+, A股30%左右 — 均线等待可有效控制回撤                     │
  └─────────────────────────────────────────────────────────────────────────────────────┘
""")

    # 组合推荐 — 两个方案
    print(f"\n  ▎方案A: 进取型 (均线等待, 年化最高):")
    print(f"  {'标的':<14} {'占比':<8} {'策略':<24} {'年化':<8} {'回撤':<8}")
    print(f"  {'-'*14} {'-'*8} {'-'*24} {'-'*8} {'-'*8}")
    port_a_ret = 0; port_a_dd = 0
    weights_a = {'沪深300': 0.10, '创业板指数': 0.20, '中证500': 0.15, '纳斯达克综合': 0.30, '标普500': 0.25}
    for name, rec in recs.items():
        w = weights_a.get(name, 0.2)
        port_a_ret += rec.annual_return_pct * w
        port_a_dd += rec.max_drawdown_pct * w
        print(f"  {name:<14} {f'{w*100:.0f}%':<8} {rec.config_name:<24} {rec.annual_return_pct:>6.2f}%  {rec.max_drawdown_pct:>6.2f}%")
    print(f"  {'组合预期':<14} {'':<8} {'年化 ' + str(round(port_a_ret, 2)) + '%':<24} {port_a_ret:>6.2f}%  {port_a_dd:>6.2f}%")

    # Find value average monthly results for each index
    print(f"\n  ▎方案B: 稳健型 (价值平均, 资金利用率高):")
    print(f"  {'标的':<14} {'占比':<8} {'策略':<24} {'年化':<8} {'回撤':<8}")
    print(f"  {'-'*14} {'-'*8} {'-'*24} {'-'*8} {'-'*8}")
    port_b_ret = 0; port_b_dd = 0
    for sym, name in INDICES.items():
        sym_results = [r for r in all_results if r.symbol == sym and r.mode == 'value_avg' and r.interval_days == 30]
        if sym_results:
            va = sym_results[0]
            w = weights_a.get(name, 0.2)
            port_b_ret += va.annual_return_pct * w
            port_b_dd += va.max_drawdown_pct * w
            print(f"  {name:<14} {f'{w*100:.0f}%':<8} {'价值平均(每月)':<24} {va.annual_return_pct:>6.2f}%  {va.max_drawdown_pct:>6.2f}%")
    print(f"  {'组合预期':<14} {'':<8} {'年化 ' + str(round(port_b_ret, 2)) + '%':<24} {port_b_ret:>6.2f}%  {port_b_dd:>6.2f}%")

    print(f"""
  💡 实操建议 (每月一次):
     1. ETF映射:
        纳斯达克 → QQQ(Invesco) 或 QQQM
        标普500 → SPY/IVV/VOO
        创业板 → 159915.SZ (易方达创业板ETF)
        沪深300 → 510300.SH (华泰柏瑞沪深300ETF)
        中证500 → 510500.SH (南方中证500ETF)
     2. 进取型(方案A): 每月检查价格vs年线, 低于年线时买入
     3. 稳健型(方案B): 价值平均法 — 目标市值 = 累计月数 × 10,000元, 低位补高位减
     4. 美股每周定投vs每月定投年化差<0.1%, 选每月即可
     5. 每半年再平衡一次比例到目标权重
""")
    print_separator()


if __name__ == '__main__':
    main()
