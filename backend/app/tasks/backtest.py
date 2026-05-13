"""
回测定时任务 - 异步执行回测
"""
from datetime import datetime, timezone
from loguru import logger

from app.core.celery_app import celery_app
from app.core.database import SyncSessionLocal


@celery_app.task(queue="backtest")
def run_async_backtest(strategy_id: int, symbol: str, interval: str, start_date: str, end_date: str, **kwargs):
    """异步执行回测任务"""
    from app.models.strategy import Strategy
    from app.services.backtest import run_backtest
    from app.services.market import get_kline_data_sync

    logger.info(f"开始回测: strategy_id={strategy_id}, symbol={symbol}, {start_date}~{end_date}")
    db = SyncSessionLocal()
    try:
        strategy = db.get(Strategy, strategy_id)
        if not strategy:
            logger.error(f"策略 {strategy_id} 不存在")
            return

        klines = get_kline_data_sync(db, symbol, interval)

        kwargs.setdefault("initial_capital", 1000000)
        kwargs.setdefault("commission_rate", 0.001)
        kwargs.setdefault("slippage", 0.001)

        import json as _json
        params = _json.loads(strategy.params) if strategy.params else {}
        result = run_backtest(
            strategy_type=strategy.type,
            params=params,
            kline_data=klines,
            initial_capital=kwargs["initial_capital"],
            commission=kwargs["commission_rate"],
            slippage=kwargs["slippage"],
        )

        # 保存回测结果
        from app.models.backtest import BacktestResult
        bt = BacktestResult(
            user_id=strategy.user_id,
            strategy_id=strategy_id,
            symbol=symbol,
            interval=interval,
            start_date=datetime.fromisoformat(start_date) if start_date else None,
            end_date=datetime.fromisoformat(end_date) if end_date else None,
            initial_capital=kwargs["initial_capital"],
            total_return=result.get("total_return", 0),
            annual_return=result.get("annual_return", 0),
            max_drawdown=result.get("max_drawdown", 0),
            sharpe_ratio=result.get("sharpe_ratio", 0),
            win_rate=result.get("win_rate", 0),
            total_trades=result.get("total_trades", 0),
            equity_curve=result.get("equity_curve", []),
            trades=result.get("trades", []),
        )
        db.add(bt)
        db.commit()
        logger.info(f"回测完成: strategy_id={strategy_id}, 收益率={result.get('total_return', 0):.2f}%")
    except Exception as e:
        db.rollback()
        logger.error(f"回测失败: {e}")
    finally:
        db.close()
