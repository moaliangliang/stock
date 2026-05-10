"""
综合回测 + 基本面分析：9 只 KDJ 金叉 + J<20 + RSI<30 的 A 股
"""
import sys, os, json, math
sys.path.insert(0, os.path.dirname(__file__))

import akshare as ak
import pandas as pd
from datetime import datetime, timedelta, timezone

from app.services.backtest import run_backtest
from app.models.strategy import StrategyType

STOCKS = [
    ("002717", "SZ", "*ST岭南"),
    ("601818", "SH", "光大银行"),
    ("002035", "SZ", "华帝股份"),
    ("605338", "SH", "巴比食品"),
    ("300003", "SZ", "乐普医疗"),
    ("688201", "SH", "ST信安"),
    ("600216", "SH", "浙江医药"),
    ("600196", "SH", "复星医药"),
    ("600734", "SH", "*ST实达"),
]

def fetch_daily_kline(code, market, name, days=500):
    """获取日K线数据"""
    symbol_map = {"SH": "sh", "SZ": "sz"}
    full_symbol = f"{symbol_map[market]}{code}"

    # 尝试多种 akshare 接口
    try:
        # 新接口: stock_zh_a_hist
        df = ak.stock_zh_a_hist(symbol=code, period="daily", start_date="20230101", end_date="20260510", adjust="qfq")
        if df is None or df.empty:
            raise Exception("stock_zh_a_hist returned empty")
    except Exception as e1:
        try:
            # 备用接口
            df = ak.stock_zh_a_daily(symbol=full_symbol, adjust="qfq")
            if df is None or df.empty:
                raise Exception("stock_zh_a_daily returned empty")
        except Exception as e2:
            return None, f"akshare failed: {e1} / {e2}"

    if df is None or df.empty:
        return None, "empty dataframe"

    # Normalize columns
    col_map = {}
    for col in df.columns:
        col_lower = col.lower()
        if 'date' in col_lower or '日期' in col:
            col_map[col] = 'date'
        elif 'open' in col_lower or '开盘' in col:
            col_map[col] = 'open'
        elif 'high' in col_lower or '最高' in col:
            col_map[col] = 'high'
        elif 'low' in col_lower or '最低' in col:
            col_map[col] = 'low'
        elif 'close' in col_lower or '收盘' in col:
            col_map[col] = 'close'
        elif 'volume' in col_lower or '成交' in col:
            col_map[col] = 'volume'

    df = df.rename(columns=col_map)

    # Build kline list for backtest
    kline_data = []
    for _, row in df.iterrows():
        try:
            ts = pd.Timestamp(row['date'])
            kline_data.append({
                "timestamp": int(ts.timestamp()),
                "open": float(row['open']),
                "high": float(row['high']),
                "low": float(row['low']),
                "close": float(row['close']),
                "volume": float(row.get('volume', 0)),
            })
        except (ValueError, KeyError):
            continue

    kline_data.sort(key=lambda x: x['timestamp'])
    return kline_data, None


def fetch_fundamentals(code, market):
    """获取基本面数据"""
    result = {}
    try:
        # 获取个股信息
        info = ak.stock_individual_info_em(symbol=code)
        if info is not None and not info.empty:
            for _, row in info.iterrows():
                key = str(row.iloc[0]).strip()
                val = row.iloc[1]
                result[key] = val
    except Exception:
        pass

    # 获取财务数据
    try:
        fin = ak.stock_financial_abstract_ths(symbol=code, indicator="按报告期")
        if fin is not None and not fin.empty:
            result['financial_table'] = fin.tail(4).to_dict('records')
    except Exception:
        pass

    return result


def main():
    results = []

    for code, market, name in STOCKS:
        print(f"\n{'='*60}")
        print(f"分析 {code} {name} ...")

        # 1. 获取K线数据
        kline_data, err = fetch_daily_kline(code, market, name, days=500)
        if err:
            print(f"  K线获取失败: {err}")
            results.append({"code": code, "name": name, "error": err})
            continue

        print(f"  K线: {len(kline_data)} 条 ({pd.to_datetime(kline_data[0]['timestamp'], unit='s').date()} ~ {pd.to_datetime(kline_data[-1]['timestamp'], unit='s').date()})")

        # 2. 运行 KDJ 回测
        bt = run_backtest(
            strategy_type=StrategyType.KDJ,
            params={"n": 9, "k": 3, "d": 3},
            kline_data=kline_data,
            initial_capital=100000.0,
            commission=0.0003,  # 万三佣金
            slippage=0.001,
        )

        # 3. 获取最新价格和技术指标
        latest = kline_data[-1]
        latest_close = latest['close']

        # 计算近期涨跌幅
        if len(kline_data) >= 5:
            close_5d = kline_data[-6]['close'] if len(kline_data) > 5 else kline_data[0]['close']
            chg_5d = (latest_close - close_5d) / close_5d * 100
        else:
            chg_5d = 0

        if len(kline_data) >= 20:
            close_20d = kline_data[-21]['close'] if len(kline_data) > 20 else kline_data[0]['close']
            chg_20d = (latest_close - close_20d) / close_20d * 100
        else:
            chg_20d = 0

        # 4. 获取基本面
        fund = fetch_fundamentals(code, market)

        pe = fund.get('市盈率-动态', fund.get('市盈率(动)', None))
        pb = fund.get('市净率', None)
        total_mv = fund.get('总市值', None)

        print(f"  最新价: {latest_close:.2f}")
        print(f"  总收益率: {bt['total_return']*100:.2f}%")
        print(f"  年化收益: {bt['annual_return']*100:.2f}%")
        print(f"  最大回撤: {bt['max_drawdown']*100:.2f}%")
        print(f"  夏普比率: {bt['sharpe_ratio']:.2f}")
        print(f"  胜率: {bt['win_rate']*100:.1f}%")
        print(f"  交易次数: {bt['total_trades']}")
        print(f"  盈亏比: {bt['profit_factor']:.2f}")

        results.append({
            "code": code,
            "name": name,
            "market": market,
            "latest_close": latest_close,
            "chg_5d_pct": round(chg_5d, 2),
            "chg_20d_pct": round(chg_20d, 2),
            "total_return_pct": round(bt['total_return']*100, 2),
            "annual_return_pct": round(bt['annual_return']*100, 2),
            "max_drawdown_pct": round(bt['max_drawdown']*100, 2),
            "sharpe_ratio": bt['sharpe_ratio'],
            "win_rate_pct": round(bt['win_rate']*100, 1),
            "total_trades": bt['total_trades'],
            "profit_trades": bt['profit_trades'],
            "loss_trades": bt['loss_trades'],
            "profit_factor": bt['profit_factor'],
            "pe": pe,
            "pb": pb,
            "total_mv": total_mv,
            "fundamentals": {k: v for k, v in fund.items() if k != 'financial_table'},
        })

    # 输出汇总
    print("\n" + "="*80)
    print("汇总结果")
    print("="*80)

    # 按总收益率排序
    results.sort(key=lambda x: x.get('total_return_pct', -999), reverse=True)

    print(f"\n{'代码':<8} {'名称':<10} {'最新价':>8} {'5日涨跌':>8} {'总收益':>8} {'年化':>8} {'最大回撤':>8} {'夏普':>6} {'胜率':>6} {'交易':>4} {'盈亏比':>6} {'PE':>8} {'PB':>6}")
    print("-" * 110)

    for r in results:
        if 'error' in r:
            print(f"{r['code']:<8} {r['name']:<10} ERROR: {r['error'][:50]}")
            continue

        pe_str = f"{float(r['pe']):.1f}" if r['pe'] and r['pe'] != '--' and r['pe'] != 'None' else 'N/A'
        pb_str = f"{float(r['pb']):.2f}" if r['pb'] and r['pb'] != '--' and r['pb'] != 'None' else 'N/A'

        print(f"{r['code']:<8} {r['name']:<10} {r['latest_close']:>8.2f} {r['chg_5d_pct']:>+7.2f}% {r['total_return_pct']:>+7.2f}% {r['annual_return_pct']:>+7.2f}% {r['max_drawdown_pct']:>7.2f}% {r['sharpe_ratio']:>6.2f} {r['win_rate_pct']:>5.1f}% {r['total_trades']:>4} {r['profit_factor']:>6.2f} {pe_str:>8} {pb_str:>6}")

    # 投资建议
    print("\n" + "="*80)
    print("投资建议 (综合回测表现 + 基本面)")
    print("="*80)

    for r in results:
        if 'error' in r:
            continue

        score = 0
        reasons = []

        # 回测因子
        if r['total_return_pct'] > 20:
            score += 2
            reasons.append("回测总收益 > 20%")
        elif r['total_return_pct'] > 0:
            score += 1
            reasons.append("回测正收益")
        else:
            score -= 1
            reasons.append("回测亏损")

        if r['sharpe_ratio'] > 0.5:
            score += 2
            reasons.append("夏普 > 0.5")
        elif r['sharpe_ratio'] > 0:
            score += 1

        if r['win_rate_pct'] > 50:
            score += 1
            reasons.append(f"胜率 {r['win_rate_pct']}%")

        if r['profit_factor'] > 1.5:
            score += 1
            reasons.append(f"盈亏比 {r['profit_factor']:.1f}")

        if r['max_drawdown_pct'] < 15:
            score += 1
            reasons.append(f"回撤可控 {r['max_drawdown_pct']:.1f}%")
        elif r['max_drawdown_pct'] > 30:
            score -= 1
            reasons.append(f"回撤较大 {r['max_drawdown_pct']:.1f}%")

        # 基本面因子
        pe_val = None
        try:
            if r['pe'] and r['pe'] != '--' and r['pe'] != 'None':
                pe_val = float(r['pe'])
        except Exception:
            pass

        if pe_val is not None:
            if pe_val < 0:
                score -= 1
                reasons.append("PE为负(亏损)")
            elif pe_val < 15:
                score += 2
                reasons.append(f"PE={pe_val:.1f} 低估值")
            elif pe_val < 25:
                score += 1
                reasons.append(f"PE={pe_val:.1f} 合理偏低")
            elif pe_val > 60:
                score -= 1
                reasons.append(f"PE={pe_val:.1f} 偏高")

        pb_val = None
        try:
            if r['pb'] and r['pb'] != '--' and r['pb'] != 'None':
                pb_val = float(r['pb'])
        except Exception:
            pass

        if pb_val is not None and pb_val < 1.5:
            score += 1
            reasons.append(f"PB={pb_val:.2f} 破净/低PB")

        # ST 股风险
        if 'ST' in r['name'] or '*ST' in r['name']:
            score -= 3
            reasons.append("⚠ ST股，退市风险高")

        # 建议
        if score >= 5:
            rec = "强烈关注 ★★★★★"
        elif score >= 3:
            rec = "建议关注 ★★★★"
        elif score >= 1:
            rec = "谨慎关注 ★★★"
        elif score >= -1:
            rec = "观望 ★★"
        else:
            rec = "不建议 ★"

        print(f"\n{r['code']} {r['name']} — {rec} (得分: {score})")
        print(f"  理由: {'; '.join(reasons)}")

    # 保存 JSON
    output = {
        "query_date": "2026-05-10",
        "data_date": "2026-05-08",
        "conditions": "KDJ金叉 + J<20 + RSI<30",
        "results": [{k: str(v) if isinstance(v, (pd.Timestamp,)) else v for k, v in r.items()} for r in results]
    }

    out_path = "/root/.openclaw/workspace/mx_data/output/stock_analysis_9.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n详细结果已保存: {out_path}")


if __name__ == "__main__":
    main()
