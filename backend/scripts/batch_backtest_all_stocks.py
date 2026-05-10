"""
批量回测所有活跃标的 × 5个经典策略，结果写入 backtest_results 表。

用法:
    DEBUG=true python scripts/batch_backtest_all_stocks.py
"""
import os
import sys
import time

os.environ.setdefault("DEBUG", "true")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.database import SyncSessionLocal
from app.services.data_provider import fetch_real_klines
from app.services.backtest import run_backtest
from app.models.strategy import StrategyType
from app.models.market_data import SymbolInfo
from app.models.backtest import BacktestResult
from sqlalchemy import select, delete

# 五策略配置
STRATEGIES = [
    (StrategyType.MA_CROSS, {"fast_period": 5, "slow_period": 20}, "MA(5,20)"),
    (StrategyType.MACD, {"fast": 12, "slow": 26, "signal": 9}, "MACD(12,26,9)"),
    (StrategyType.KDJ, {"n": 9, "k": 3, "d": 3}, "KDJ(9,3,3)"),
    (StrategyType.BOLLINGER, {"period": 20, "std": 2.0}, "BOLL(20,2)"),
    (StrategyType.GRID, {"grid_levels": 10}, "GRID(10)"),
]

INITIAL_CAPITAL = 10000.0
COMMISSION = 0.001
SLIPPAGE = 0.001


def main():
    db = SyncSessionLocal()
    try:
        # 获取所有活跃标的
        symbols = db.execute(
            select(SymbolInfo).where(SymbolInfo.status == "active")
        ).scalars().all()
        print(f"活跃标的: {len(symbols)} 个")
        print(f"策略数: {len(STRATEGIES)} 个")
        print(f"总计: {len(symbols) * len(STRATEGIES)} 个回测任务")
        print()

        # 清空旧结果
        deleted = db.execute(delete(BacktestResult)).rowcount
        db.commit()
        print(f"已清理旧回测结果 {deleted} 条\n")

        total = len(symbols) * len(STRATEGIES)
        done = 0
        failed = 0

        for sym in symbols:
            symbol = sym.symbol
            name = sym.name or symbol
            print(f"\n{'='*60}")
            print(f"  {symbol} {name}")
            print(f"{'='*60}")

            # 拉取日线数据
            klines = fetch_real_klines(symbol, "1d")
            if not klines:
                print(f"  ⨯ 无K线数据, 跳过")
                failed += len(STRATEGIES)
                continue

            data_start = klines[0]["timestamp"].strftime("%Y-%m-%d") if hasattr(klines[0]["timestamp"], "strftime") else str(klines[0]["timestamp"])[:10]
            data_end = klines[-1]["timestamp"].strftime("%Y-%m-%d") if hasattr(klines[-1]["timestamp"], "strftime") else str(klines[-1]["timestamp"])[:10]
            print(f"  K线: {len(klines)} 条 ({data_start} ~ {data_end})")

            for stype, params, label in STRATEGIES:
                try:
                    # 网格策略需要上下界
                    if stype == StrategyType.GRID:
                        closes = [k["close"] for k in klines if k.get("close")]
                        if closes:
                            mid = sum(closes) / len(closes)
                            params = {
                                "grid_levels": 10,
                                "upper_price": round(mid * 1.5, 2),
                                "lower_price": round(mid * 0.5, 2),
                            }

                    t0 = time.time()
                    result = run_backtest(
                        strategy_type=stype,
                        params=params,
                        kline_data=klines,
                        initial_capital=INITIAL_CAPITAL,
                        commission=COMMISSION,
                        slippage=SLIPPAGE,
                    )
                    elapsed = time.time() - t0

                    # 写入 DB
                    db.add(BacktestResult(
                        symbol=symbol,
                        strategy_type=stype.value,
                        strategy_params=label,
                        total_return=result["total_return"],
                        annual_return=result["annual_return"],
                        max_drawdown=result["max_drawdown"],
                        sharpe_ratio=result["sharpe_ratio"],
                        final_equity=result["final_equity"],
                        win_rate=result["win_rate"],
                        total_trades=result["total_trades"],
                        profit_trades=result["profit_trades"],
                        loss_trades=result["loss_trades"],
                        profit_factor=result["profit_factor"],
                        kline_count=len(klines),
                        data_start=data_start,
                        data_end=data_end,
                    ))
                    db.commit()

                    done += 1
                    ret_pct = result["total_return"] * 100
                    print(f"  [{done}/{total}] {label:16s} 收益{ret_pct:+7.1f}%  年化{result['annual_return']*100:+6.1f}%  "
                          f"回撤{result['max_drawdown']*100:5.1f}%  夏普{result['sharpe_ratio']:5.2f}  "
                          f"胜率{result['win_rate']*100:4.0f}%  ({elapsed:.1f}s)")

                except Exception as e:
                    failed += 1
                    print(f"  ⨯ {label}: {e}")

        print(f"\n{'='*60}")
        print(f"完成: {done} 成功, {failed} 失败")
        print(f"{'='*60}")

    finally:
        db.close()


if __name__ == "__main__":
    main()
