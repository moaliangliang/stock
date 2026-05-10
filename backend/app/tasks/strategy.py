"""
策略定时任务 - 定时运行策略
"""
import asyncio
from datetime import datetime, timezone
from loguru import logger

from app.core.celery_app import celery_app
from app.core.database import SyncSessionLocal


def _run_async(coro):
    """在同步上下文中运行异步协程"""
    return asyncio.run(coro)


@celery_app.task(queue="strategy")
def run_scheduled_strategies():
    """定时运行所有活跃策略"""
    logger.info(f"开始执行定时策略: {datetime.now(timezone.utc)}")
    db = SyncSessionLocal()
    try:
        from sqlalchemy import select
        from app.models.strategy import Strategy, StrategyStatus, StrategyRunLog
        from app.services.strategy import run_strategy_logic

        stmt = select(Strategy).where(Strategy.status == StrategyStatus.ACTIVE)
        strategies = list(db.execute(stmt).scalars().all())
        logger.info(f"找到 {len(strategies)} 个活跃策略")

        for strategy in strategies:
            schedule_config = strategy.schedule_config or {}
            if not schedule_config.get("enabled", False):
                continue

            symbols = strategy.symbols or []
            intervals = strategy.intervals or []
            if not symbols or not intervals:
                continue

            try:
                from app.services.strategy import _kline_to_dataframe
                from app.models.market_data import KLine

                # 查询 K 线数据（同步）
                klines_raw = db.execute(
                    select(KLine).where(
                        KLine.symbol == symbols[0],
                        KLine.interval == intervals[0],
                    ).order_by(KLine.timestamp.desc()).limit(200)
                ).scalars().all()

                if not klines_raw:
                    logger.warning(f"策略 {strategy.id} 无K线数据")
                    continue

                klines = [
                    {
                        "timestamp": k.timestamp,
                        "open": k.open,
                        "high": k.high,
                        "low": k.low,
                        "close": k.close,
                        "volume": k.volume,
                    }
                    for k in reversed(klines_raw)
                ]
                df = _kline_to_dataframe(klines)

                # 执行策略
                from app.services.strategy import (
                    ma_cross_strategy, macd_strategy, kdj_strategy,
                    bollinger_strategy, grid_strategy,
                    StrategyType,
                )

                params = strategy.params or {}
                signals = []

                if strategy.type == StrategyType.MA_CROSS:
                    signals = ma_cross_strategy(df, fast_period=params.get("fast_period", 5), slow_period=params.get("slow_period", 20))
                elif strategy.type == StrategyType.MACD:
                    signals = macd_strategy(df, fast=params.get("fast", 12), slow=params.get("slow", 26), signal=params.get("signal", 9))
                elif strategy.type == StrategyType.KDJ:
                    signals = kdj_strategy(df, n=params.get("n", 9), k=params.get("k", 3), d=params.get("d", 3))
                elif strategy.type == StrategyType.BOLLINGER:
                    signals = bollinger_strategy(df, period=params.get("period", 20), std=params.get("std", 2))
                elif strategy.type == StrategyType.GRID:
                    signals = grid_strategy(df, grid_levels=params.get("grid_levels", 10), upper_price=params.get("upper_price"), lower_price=params.get("lower_price"))

                log = StrategyRunLog(
                    strategy_id=strategy.id,
                    status="success",
                    signals=signals,
                    message=f"信号数: {len(signals)}",
                    duration_ms=0,
                )
                db.add(log)
                db.commit()
                logger.info(f"策略 {strategy.id}({strategy.name}) 执行完成，{len(signals)} 个信号")

            except Exception as e:
                logger.error(f"策略 {strategy.id} 执行失败: {e}")
                db.rollback()
    except Exception as e:
        logger.error(f"策略调度失败: {e}")
    finally:
        db.close()


@celery_app.task(queue="strategy")
def run_single_strategy(strategy_id: int):
    """运行单个策略"""
    logger.info(f"执行策略 ID={strategy_id}")
    db = SyncSessionLocal()
    try:
        from app.models.strategy import Strategy, StrategyStatus, StrategyRunLog
        from app.models.market_data import KLine
        from app.services.strategy import (
            ma_cross_strategy, macd_strategy, kdj_strategy,
            bollinger_strategy, grid_strategy,
            StrategyType, _kline_to_dataframe,
        )

        strategy = db.get(Strategy, strategy_id)
        if not strategy:
            logger.error(f"策略 {strategy_id} 不存在")
            return

        symbols = strategy.symbols or []
        intervals = strategy.intervals or []
        if not symbols or not intervals:
            return

        klines_raw = db.execute(
            select(KLine).where(
                KLine.symbol == symbols[0],
                KLine.interval == intervals[0],
            ).order_by(KLine.timestamp.desc()).limit(200)
        ).scalars().all()

        if not klines_raw:
            logger.warning(f"策略 {strategy_id} 无K线数据")
            return

        klines = [
            {"timestamp": k.timestamp, "open": k.open, "high": k.high,
             "low": k.low, "close": k.close, "volume": k.volume}
            for k in reversed(klines_raw)
        ]
        df = _kline_to_dataframe(klines)

        params = strategy.params or {}
        signals = []

        if strategy.type == StrategyType.MA_CROSS:
            signals = ma_cross_strategy(df, fast_period=params.get("fast_period", 5), slow_period=params.get("slow_period", 20))
        elif strategy.type == StrategyType.MACD:
            signals = macd_strategy(df, fast=params.get("fast", 12), slow=params.get("slow", 26), signal=params.get("signal", 9))
        elif strategy.type == StrategyType.KDJ:
            signals = kdj_strategy(df, n=params.get("n", 9), k=params.get("k", 3), d=params.get("d", 3))
        elif strategy.type == StrategyType.BOLLINGER:
            signals = bollinger_strategy(df, period=params.get("period", 20), std=params.get("std", 2))
        elif strategy.type == StrategyType.GRID:
            signals = grid_strategy(df, grid_levels=params.get("grid_levels", 10), upper_price=params.get("upper_price"), lower_price=params.get("lower_price"))

        log = StrategyRunLog(
            strategy_id=strategy.id,
            status="success",
            signals=signals,
            message=f"手动执行成功，{len(signals)} 个信号",
            duration_ms=0,
        )
        db.add(log)
        db.commit()
        logger.info(f"策略 {strategy_id} 执行完成")
    except Exception as e:
        logger.error(f"策略 {strategy_id} 执行失败: {e}")
        db.rollback()
    finally:
        db.close()
