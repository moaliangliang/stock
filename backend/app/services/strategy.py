"""
Strategy service - CRUD, execution engine, and built-in strategy templates."""
from __future__ import annotations
import math
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from sqlalchemy import and_, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.strategy import (
    Strategy,
    StrategyRunLog,
    StrategyStatus,
    StrategyType,
)


# ---------------------------------------------------------------------------
# CRUD operations
# ---------------------------------------------------------------------------

async def get_strategies(
    db: AsyncSession,
    user_id: Optional[int] = None,
    status: Optional[StrategyStatus] = None,
    skip: int = 0,
    limit: int = 100,
) -> List[Strategy]:
    """
    List strategies with optional filtering.

    Args:
        db: Database session.
        user_id: Filter by user ID (optional).
        status: Filter by strategy status (optional).
        skip: Number of records to skip.
        limit: Maximum number of records to return.

    Returns:
        A list of Strategy objects.
    """
    query = select(Strategy)

    if user_id is not None:
        query = query.where(Strategy.user_id == user_id)
    if status is not None:
        query = query.where(Strategy.status == status)

    query = query.order_by(desc(Strategy.created_at)).offset(skip).limit(limit)
    result = await db.execute(query)
    return list(result.scalars().all())


async def get_strategy(db: AsyncSession, strategy_id: int) -> Optional[Strategy]:
    """
    Get a single strategy by ID.

    Args:
        db: Database session.
        strategy_id: Strategy ID.

    Returns:
        The Strategy object if found, None otherwise.
    """
    result = await db.execute(select(Strategy).where(Strategy.id == strategy_id))
    return result.scalar_one_or_none()


async def create_strategy(db: AsyncSession, user_id: int, strategy_data: dict) -> Strategy:
    """
    Create a new strategy.

    Args:
        db: Database session.
        user_id: Owner user ID.
        strategy_data: Dictionary with fields: name, type, description, params,
                       symbols, intervals, initial_capital, etc.

    Returns:
        The newly created Strategy object.
    """
    strategy = Strategy(
        user_id=user_id,
        name=strategy_data["name"],
        type=strategy_data["type"],
        description=strategy_data.get("description"),
        status=StrategyStatus.DRAFT,
        params=strategy_data.get("params", {}),
        symbols=strategy_data.get("symbols", []),
        intervals=strategy_data.get("intervals", ["1d"]),
        initial_capital=strategy_data.get("initial_capital", 10000.0),
        max_position_ratio=strategy_data.get("max_position_ratio", 30),
        is_custom_code=strategy_data.get("is_custom_code", False),
        custom_code=strategy_data.get("custom_code"),
    )
    db.add(strategy)
    await db.flush()
    await db.refresh(strategy)
    return strategy


async def update_strategy(
    db: AsyncSession, strategy_id: int, strategy_data: dict
) -> Optional[Strategy]:
    """
    Update an existing strategy.

    Args:
        db: Database session.
        strategy_id: Strategy ID.
        strategy_data: Dictionary of fields to update.

    Returns:
        The updated Strategy object if found, None otherwise.
    """
    result = await db.execute(select(Strategy).where(Strategy.id == strategy_id))
    strategy = result.scalar_one_or_none()
    if strategy is None:
        return None

    allowed_fields = {
        "name", "description", "params", "symbols", "intervals",
        "initial_capital", "max_position_ratio", "status",
        "is_custom_code", "custom_code", "schedule_config",
    }

    for key, value in strategy_data.items():
        if key in allowed_fields:
            setattr(strategy, key, value)

    await db.flush()
    await db.refresh(strategy)
    return strategy


async def delete_strategy(db: AsyncSession, strategy_id: int) -> bool:
    """
    Delete a strategy by ID.

    Args:
        db: Database session.
        strategy_id: Strategy ID.

    Returns:
        True if the strategy was deleted, False if not found.
    """
    result = await db.execute(select(Strategy).where(Strategy.id == strategy_id))
    strategy = result.scalar_one_or_none()
    if strategy is None:
        return False

    await db.delete(strategy)
    await db.flush()
    return True


async def create_run_log(
    db: AsyncSession,
    strategy_id: int,
    status: str = "success",
    message: Optional[str] = None,
    signals: Optional[list] = None,
    duration_ms: Optional[int] = None,
) -> StrategyRunLog:
    """
    Record a strategy run log entry.

    Args:
        db: Database session.
        strategy_id: Strategy ID.
        status: "success" or "error".
        message: Optional status message.
        signals: List of signals generated.
        duration_ms: Execution duration in milliseconds.

    Returns:
        The created StrategyRunLog object.
    """
    run_log = StrategyRunLog(
        strategy_id=strategy_id,
        run_time=datetime.now(timezone.utc),
        status=status,
        message=message,
        signals=signals or [],
        duration_ms=duration_ms,
    )
    db.add(run_log)
    await db.flush()
    await db.refresh(run_log)
    return run_log


# ---------------------------------------------------------------------------
# Core strategy execution engine
# ---------------------------------------------------------------------------

async def run_strategy_logic(
    strategy: Strategy, kline_data: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """
    Core strategy execution engine.

    Routes to the appropriate built-in strategy template based on
    *strategy.type* and returns the generated signals.

    Args:
        strategy: The Strategy model instance containing type and params.
        kline_data: A list of kline dicts with keys:
            timestamp, open, high, low, close, volume (and optionally amount).

    Returns:
        A dict with keys:
            - signals: list of signal dicts
            - metadata: dict with strategy info
    """
    df = _kline_to_dataframe(kline_data)
    params = strategy.params or {}
    signals: List[Dict[str, Any]] = []

    start_time = time.time()

    if strategy.type == StrategyType.MA_CROSS:
        fast_period = params.get("fast_period", 5)
        slow_period = params.get("slow_period", 20)
        signals = ma_cross_strategy(df, fast_period=fast_period, slow_period=slow_period)

    elif strategy.type == StrategyType.MACD:
        fast = params.get("fast", 12)
        slow = params.get("slow", 26)
        signal = params.get("signal", 9)
        signals = macd_strategy(df, fast=fast, slow=slow, signal=signal)

    elif strategy.type == StrategyType.KDJ:
        n = params.get("n", 9)
        k = params.get("k", 3)
        d = params.get("d", 3)
        signals = kdj_strategy(df, n=n, k=k, d=d)

    elif strategy.type == StrategyType.BOLLINGER:
        period = params.get("period", 20)
        std = params.get("std", 2)
        signals = bollinger_strategy(df, period=period, std=std)

    elif strategy.type == StrategyType.GRID:
        grid_levels = params.get("grid_levels", 10)
        upper_price = params.get("upper_price")
        lower_price = params.get("lower_price")
        signals = grid_strategy(
            df, grid_levels=grid_levels,
            upper_price=upper_price, lower_price=lower_price,
        )

    else:
        # Unknown / custom strategies return no signals
        signals = []

    elapsed_ms = int((time.time() - start_time) * 1000)

    return {
        "signals": signals,
        "metadata": {
            "strategy_id": strategy.id,
            "strategy_name": strategy.name,
            "strategy_type": strategy.type.value,
            "bars_analyzed": len(df),
            "signals_generated": len(signals),
            "duration_ms": elapsed_ms,
        },
    }


# ---------------------------------------------------------------------------
# Built-in strategy templates
# ---------------------------------------------------------------------------

def ma_cross_strategy(
    data: pd.DataFrame,
    fast_period: int = 5,
    slow_period: int = 20,
) -> List[Dict[str, Any]]:
    """
    Moving Average Crossover strategy.

    Generates a BUY signal when the fast MA crosses above the slow MA,
    and a SELL signal when the fast MA crosses below the slow MA.

    Args:
        data: DataFrame with at least a 'close' column.
        fast_period: Fast MA period.
        slow_period: Slow MA period.

    Returns:
        List of signal dicts: {timestamp, action, price, reason}.
    """
    import pandas as pd
    df = data.copy()
    if len(df) < slow_period + 1:
        return []

    df["ma_fast"] = df["close"].rolling(window=fast_period).mean()
    df["ma_slow"] = df["close"].rolling(window=slow_period).mean()
    df["prev_fast"] = df["ma_fast"].shift(1)
    df["prev_slow"] = df["ma_slow"].shift(1)

    signals: List[Dict[str, Any]] = []

    for idx in df.iterrows():
        i = idx[0]
        row = idx[1]
        if pd.isna(row.get("prev_fast")) or pd.isna(row.get("prev_slow")):
            continue

        if row["prev_fast"] <= row["prev_slow"] and row["ma_fast"] > row["ma_slow"]:
            signals.append({
                "timestamp": row.get("timestamp"),
                "action": "buy",
                "price": row["close"],
                "reason": f"MA cross up (fast={fast_period}, slow={slow_period})",
            })

        elif row["prev_fast"] >= row["prev_slow"] and row["ma_fast"] < row["ma_slow"]:
            signals.append({
                "timestamp": row.get("timestamp"),
                "action": "sell",
                "price": row["close"],
                "reason": f"MA cross down (fast={fast_period}, slow={slow_period})",
            })

    return signals


def macd_strategy(
    data: pd.DataFrame,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> List[Dict[str, Any]]:
    """
    MACD (Moving Average Convergence Divergence) strategy.

    Generates BUY when the MACD line crosses above the signal line,
    and SELL when it crosses below.

    Args:
        data: DataFrame with a 'close' column.
        fast: Fast EMA period.
        slow: Slow EMA period.
        signal: Signal line period.

    Returns:
        List of signal dicts.
    """
    import pandas as pd
    df = data.copy()
    if len(df) < slow + signal + 1:
        return []

    ema_fast = df["close"].ewm(span=fast, adjust=False).mean()
    ema_slow = df["close"].ewm(span=slow, adjust=False).mean()
    df["macd"] = ema_fast - ema_slow
    df["macd_signal"] = df["macd"].ewm(span=signal, adjust=False).mean()
    df["macd_hist"] = df["macd"] - df["macd_signal"]
    df["prev_macd"] = df["macd"].shift(1)
    df["prev_signal"] = df["macd_signal"].shift(1)

    signals: List[Dict[str, Any]] = []

    for idx in df.iterrows():
        i = idx[0]
        row = idx[1]
        if pd.isna(row.get("prev_macd")) or pd.isna(row.get("prev_signal")):
            continue

        if row["prev_macd"] <= row["prev_signal"] and row["macd"] > row["macd_signal"]:
            signals.append({
                "timestamp": row.get("timestamp"),
                "action": "buy",
                "price": row["close"],
                "reason": f"MACD bull cross (DIF={row['macd']:.2f}, DEA={row['macd_signal']:.2f})",
            })

        elif row["prev_macd"] >= row["prev_signal"] and row["macd"] < row["macd_signal"]:
            signals.append({
                "timestamp": row.get("timestamp"),
                "action": "sell",
                "price": row["close"],
                "reason": f"MACD bear cross (DIF={row['macd']:.2f}, DEA={row['macd_signal']:.2f})",
            })

    return signals


def kdj_strategy(
    data: pd.DataFrame,
    n: int = 9,
    k: int = 3,
    d: int = 3,
) -> List[Dict[str, Any]]:
    """
    KDJ (Stochastic Oscillator) strategy.

    Generates BUY when K crosses above D below the 20 oversold line,
    and SELL when K crosses below D above the 80 overbought line.

    Args:
        data: DataFrame with columns: high, low, close.
        n: RSV period.
        k: K smoothing period.
        d: D smoothing period.

    Returns:
        List of signal dicts.
    """
    import pandas as pd
    df = data.copy()
    if len(df) < n + max(k, d) + 1:
        return []

    low_n = df["low"].rolling(window=n).min()
    high_n = df["high"].rolling(window=n).max()

    rsv = ((df["close"] - low_n) / (high_n - low_n)) * 100
    rsv = rsv.fillna(50)

    df["kdj_k"] = rsv.ewm(adjust=False, alpha=1.0 / k).mean()
    df["kdj_d"] = df["kdj_k"].ewm(adjust=False, alpha=1.0 / d).mean()
    df["kdj_j"] = 3 * df["kdj_k"] - 2 * df["kdj_d"]
    df["prev_k"] = df["kdj_k"].shift(1)
    df["prev_d"] = df["kdj_d"].shift(1)

    signals: List[Dict[str, Any]] = []

    for idx in df.iterrows():
        i = idx[0]
        row = idx[1]
        if pd.isna(row.get("prev_k")) or pd.isna(row.get("prev_d")):
            continue

        if (
            row["prev_k"] <= row["prev_d"]
            and row["kdj_k"] > row["kdj_d"]
            and row["kdj_k"] < 20
        ):
            signals.append({
                "timestamp": row.get("timestamp"),
                "action": "buy",
                "price": row["close"],
                "reason": f"KDJ oversold cross (K={row['kdj_k']:.1f}, D={row['kdj_d']:.1f}, J={row['kdj_j']:.1f})",
            })

        elif (
            row["prev_k"] >= row["prev_d"]
            and row["kdj_k"] < row["kdj_d"]
            and row["kdj_k"] > 80
        ):
            signals.append({
                "timestamp": row.get("timestamp"),
                "action": "sell",
                "price": row["close"],
                "reason": f"KDJ overbought cross (K={row['kdj_k']:.1f}, D={row['kdj_d']:.1f}, J={row['kdj_j']:.1f})",
            })

    return signals


def bollinger_strategy(
    data: pd.DataFrame,
    period: int = 20,
    std: float = 2.0,
) -> List[Dict[str, Any]]:
    """
    Bollinger Bands strategy.

    Generates BUY when price touches or crosses below the lower band,
    and SELL when price touches or crosses above the upper band.

    Args:
        data: DataFrame with a 'close' column.
        period: Rolling window period.
        std: Number of standard deviations for the bands.

    Returns:
        List of signal dicts.
    """
    import pandas as pd
    df = data.copy()
    if len(df) < period + 1:
        return []

    df["bb_mid"] = df["close"].rolling(window=period).mean()
    df["bb_std"] = df["close"].rolling(window=period).std(ddof=0)
    df["bb_upper"] = df["bb_mid"] + std * df["bb_std"]
    df["bb_lower"] = df["bb_mid"] - std * df["bb_std"]
    df["prev_close"] = df["close"].shift(1)

    signals: List[Dict[str, Any]] = []

    for idx in df.iterrows():
        i = idx[0]
        row = idx[1]
        if pd.isna(row.get("bb_lower")) or pd.isna(row.get("bb_upper")):
            continue

        prev_close = row["prev_close"]
        curr_close = row["close"]

        # Price crosses below lower band => oversold / buy
        if prev_close >= row["bb_lower"] and curr_close < row["bb_lower"]:
            signals.append({
                "timestamp": row.get("timestamp"),
                "action": "buy",
                "price": curr_close,
                "reason": f"Price touched lower band ({row['bb_lower']:.2f})",
            })

        # Price crosses above upper band => overbought / sell
        if prev_close <= row["bb_upper"] and curr_close > row["bb_upper"]:
            signals.append({
                "timestamp": row.get("timestamp"),
                "action": "sell",
                "price": curr_close,
                "reason": f"Price touched upper band ({row['bb_upper']:.2f})",
            })

        # Price bounces from lower band
        if (
            prev_close <= row["bb_lower"]
            and curr_close > row["bb_lower"]
            and curr_close < row["bb_mid"]
        ):
            signals.append({
                "timestamp": row.get("timestamp"),
                "action": "buy",
                "price": curr_close,
                "reason": f"Bounce from lower band ({row['bb_lower']:.2f})",
            })

    return signals


def grid_strategy(
    data: pd.DataFrame,
    grid_levels: int = 10,
    upper_price: Optional[float] = None,
    lower_price: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """
    Grid trading strategy.

    Places grid lines evenly between *lower_price* and *upper_price*.
    Generates BUY signals when price crosses a grid line downward,
    and SELL signals when price crosses a grid line upward.

    If bounds are not provided they are inferred from the data range
    with a 10 % margin.

    Args:
        data: DataFrame with a 'close' column.
        grid_levels: Number of grid price levels.
        upper_price: Upper bound for the grid.
        lower_price: Lower bound for the grid.

    Returns:
        List of signal dicts.
    """
    df = data.copy()
    if len(df) < 2:
        return []

    if lower_price is None:
        lower_price = df["close"].min() * 0.9
    if upper_price is None:
        upper_price = df["close"].max() * 1.1

    if upper_price <= lower_price:
        return []

    grid_step = (upper_price - lower_price) / grid_levels
    grid_prices = [lower_price + i * grid_step for i in range(grid_levels + 1)]

    signals: List[Dict[str, Any]] = []
    prev_price = df["close"].iloc[0]

    for idx in df.iterrows():
        i = idx[0]
        row = idx[1]
        curr_price = row["close"]

        for gp in grid_prices[1:-1]:  # skip outermost bounds
            # Price crosses grid level upward -> sell
            if prev_price <= gp <= curr_price:
                signals.append({
                    "timestamp": row.get("timestamp"),
                    "action": "sell",
                    "price": curr_price,
                    "reason": f"Grid sell at {gp:.2f}",
                })
            # Price crosses grid level downward -> buy
            elif curr_price <= gp <= prev_price:
                signals.append({
                    "timestamp": row.get("timestamp"),
                    "action": "buy",
                    "price": curr_price,
                    "reason": f"Grid buy at {gp:.2f}",
                })

        prev_price = curr_price

    return signals


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def get_strategy_by_id(db: AsyncSession, strategy_id: int) -> Optional[Strategy]:
    """按 ID 获取策略（别名函数，兼容端点调用）"""
    return await get_strategy(db, strategy_id)


async def run_strategy(db: AsyncSession, strategy_id: int) -> dict:
    """
    运行指定策略并记录日志
    Returns:
        dict with status and signals
    """
    strategy = await get_strategy(db, strategy_id)
    if not strategy:
        raise ValueError(f"策略不存在: {strategy_id}")

    start = time.time()
    try:
        # 从数据库加载策略对应标的和周期的 K 线数据
        kline_data = []
        symbols = strategy.symbols or []
        intervals = strategy.intervals or ["1d"]
        if symbols:
            from app.models.market_data import KLine as KLineModel
            result = await db.execute(
                select(KLineModel)
                .where(
                    KLineModel.symbol.in_(symbols),
                    KLineModel.interval == intervals[0],
                )
                .order_by(KLineModel.timestamp.asc())
                .limit(500)
            )
            rows = list(result.scalars().all())
            kline_data = [
                {
                    "timestamp": r.timestamp,
                    "open": r.open, "high": r.high, "low": r.low,
                    "close": r.close, "volume": r.volume,
                }
                for r in rows
            ]
        signals = await run_strategy_logic(strategy, kline_data)
        elapsed = int((time.time() - start) * 1000)

        await create_run_log(db, strategy_id=strategy_id, status="success",
                             message="策略运行成功", signals=signals, duration_ms=elapsed)
        return {"status": "success", "signals": signals}
    except Exception as e:
        elapsed = int((time.time() - start) * 1000)
        await create_run_log(db, strategy_id=strategy_id, status="error",
                             message=str(e), signals=None, duration_ms=elapsed)
        raise


async def get_strategy_logs(db: AsyncSession, strategy_id: int, skip: int = 0, limit: int = 50) -> list:
    """获取策略运行日志"""
    result = await db.execute(
        select(StrategyRunLog)
        .where(StrategyRunLog.strategy_id == strategy_id)
        .order_by(desc(StrategyRunLog.run_time))
        .offset(skip).limit(limit)
    )
    return list(result.scalars().all())


async def get_strategy_templates() -> list[dict]:
    """获取内置策略模板列表"""
    return [
        {"type": "ma_cross", "name": "均线交叉", "description": "快慢均线交叉产生买卖信号", "default_params": {"fast_period": 5, "slow_period": 20}},
        {"type": "macd", "name": "MACD", "description": "MACD DIF与DEA交叉策略", "default_params": {"fast": 12, "slow": 26, "signal": 9}},
        {"type": "kdj", "name": "KDJ", "description": "随机指标KDJ超买超卖策略", "default_params": {"n": 9, "k": 3, "d": 3}},
        {"type": "bollinger", "name": "布林带", "description": "布林带上下轨突破策略", "default_params": {"period": 20, "std": 2}},
        {"type": "grid", "name": "网格交易", "description": "设定价格区间网格低买高卖", "default_params": {"grid_levels": 10, "upper_price": 0, "lower_price": 0}},
        {"type": "martingale", "name": "马丁格尔", "description": "亏损后加倍加仓的策略", "default_params": {"multiplier": 2, "max_levels": 5}},
        {"type": "trend_break", "name": "趋势突破", "description": "突破近期高点/低点跟踪趋势", "default_params": {"lookback": 20}},
    ]


# ---------------------------------------------------------------------------
# Classic strategies query & regression testing
# ---------------------------------------------------------------------------

CLASSIC_STRATEGIES = [
    {
        "type": "ma_cross",
        "name": "均线交叉",
        "description": "经典的双均线交叉策略。当快速均线上穿慢速均线时买入，下穿时卖出。适用于趋势明显的市场环境。",
        "suitable_market": "趋势市",
        "params_description": {
            "fast_period": "快线周期（默认5）",
            "slow_period": "慢线周期（默认20）",
        },
        "default_params": {"fast_period": 5, "slow_period": 20},
        "performance_metrics": {"win_rate": "~55%", "avg_return": "中等", "max_drawdown": "中等"},
    },
    {
        "type": "macd",
        "name": "MACD",
        "description": "MACD指标交叉策略。DIF线向上突破DEA线时买入，向下突破时卖出。捕捉中期趋势转折点。",
        "suitable_market": "趋势市",
        "params_description": {
            "fast": "快线EMA周期（默认12）",
            "slow": "慢线EMA周期（默认26）",
            "signal": "信号线周期（默认9）",
        },
        "default_params": {"fast": 12, "slow": 26, "signal": 9},
        "performance_metrics": {"win_rate": "~50%", "avg_return": "中等偏高", "max_drawdown": "中等"},
    },
    {
        "type": "kdj",
        "name": "KDJ",
        "description": "随机指标KDJ超买超卖策略。K线在20以下上穿D线时买入（超卖区金叉），K线在80以上下穿D线时卖出（超买区死叉）。",
        "suitable_market": "震荡市",
        "params_description": {
            "n": "RSV周期（默认9）",
            "k": "K线平滑周期（默认3）",
            "d": "D线平滑周期（默认3）",
        },
        "default_params": {"n": 9, "k": 3, "d": 3},
        "performance_metrics": {"win_rate": "~45%", "avg_return": "较低", "max_drawdown": "较低"},
    },
    {
        "type": "bollinger",
        "name": "布林带",
        "description": "布林带通道突破策略。价格触及下轨时买入（超卖），价格触及上轨时卖出（超买），价格从下轨反弹时加仓。",
        "suitable_market": "震荡市",
        "params_description": {
            "period": "中轨周期（默认20）",
            "std": "标准差倍数（默认2）",
        },
        "default_params": {"period": 20, "std": 2},
        "performance_metrics": {"win_rate": "~50%", "avg_return": "中等", "max_drawdown": "较低"},
    },
    {
        "type": "grid",
        "name": "网格交易",
        "description": "设定价格区间，在区间内等间距分网格。价格下行穿过网格线买入、上行穿过网格线卖出，实现区间内的高抛低吸。",
        "suitable_market": "震荡市",
        "params_description": {
            "grid_levels": "网格层数（默认10）",
            "upper_price": "区间上界（默认自动）",
            "lower_price": "区间下界（默认自动）",
        },
        "default_params": {"grid_levels": 10, "upper_price": 0, "lower_price": 0},
        "performance_metrics": {"win_rate": "~60%", "avg_return": "稳定偏低", "max_drawdown": "低"},
    },
    {
        "type": "martingale",
        "name": "马丁格尔",
        "description": "马丁格尔策略。每次亏损后在下次交易中加倍仓位，直到盈利后恢复初始仓位。高风险高回报，需要充足资金。",
        "suitable_market": "单边行情",
        "params_description": {
            "multiplier": "加仓倍数（默认2）",
            "max_levels": "最大加仓层数（默认5）",
        },
        "default_params": {"multiplier": 2, "max_levels": 5},
        "performance_metrics": {"win_rate": "~70%", "avg_return": "高", "max_drawdown": "极高"},
    },
    {
        "type": "trend_break",
        "name": "趋势突破",
        "description": "趋势跟踪突破策略。价格突破近期最高点时买入（向上突破），跌破近期最低点时卖出（向下突破）。适合有明显趋势的行情。",
        "suitable_market": "趋势市",
        "params_description": {
            "lookback": "回溯周期（默认20）",
        },
        "default_params": {"lookback": 20},
        "performance_metrics": {"win_rate": "~40%", "avg_return": "高", "max_drawdown": "高"},
    },
]


async def get_classic_strategies() -> list[dict]:
    """获取经典策略列表（含详细描述、参数说明、适用场景）"""
    return CLASSIC_STRATEGIES


async def run_regression_test(db: AsyncSession) -> dict:
    """对所有经典策略运行回归测试"""
    from app.services.market import mock_market_data

    # 生成标准测试数据集
    test_data = mock_market_data(
        base_price=50000.0,
        days=365,
        interval_minutes=60,
        volatility=0.03,
    )

    results = []
    for classic in CLASSIC_STRATEGIES:
        strategy_type = StrategyType(classic["type"])
        params = classic["default_params"]

        # 构造临时策略对象用于执行（user_id=0 表示系统级测试）
        temp_strategy = Strategy(
            id=0,
            user_id=0,
            name=classic["name"],
            type=strategy_type,
            params=params,
        )

        try:
            result = await run_strategy_logic(temp_strategy, test_data)
            signals = result["signals"]
            metadata = result["metadata"]

            buy_signals = [s for s in signals if s["action"] == "buy"]
            sell_signals = [s for s in signals if s["action"] == "sell"]

            results.append({
                "type": classic["type"],
                "name": classic["name"],
                "status": "success",
                "total_signals": len(signals),
                "buy_signals": len(buy_signals),
                "sell_signals": len(sell_signals),
                "bars_analyzed": metadata["bars_analyzed"],
                "duration_ms": metadata["duration_ms"],
            })
        except Exception as e:
            results.append({
                "type": classic["type"],
                "name": classic["name"],
                "status": "error",
                "error": str(e),
            })

    return {
        "test_dataset": {
            "base_price": 50000.0,
            "days": 365,
            "interval": "60m",
            "total_bars": len(test_data),
        },
        "results": results,
        "summary": {
            "total": len(results),
            "passed": sum(1 for r in results if r["status"] == "success"),
            "failed": sum(1 for r in results if r["status"] == "error"),
        },
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _kline_to_dataframe(kline_data: List[Dict[str, Any]]) -> pd.DataFrame:
    """
    Convert a list of kline dicts to a pandas DataFrame with a 'timestamp'
    column (as datetime) and standard OHLCV columns.
    """
    import pandas as pd
    df = pd.DataFrame(kline_data)
    if df.empty:
        return df

    # Normalise timestamp to datetime
    if "timestamp" in df.columns:
        if df["timestamp"].dtype in (np.int64, np.float64):
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s", utc=True)
        else:
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)

    # Ensure numeric columns
    for col in ["open", "high", "low", "close", "volume"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    if "timestamp" in df.columns:
        df = df.sort_values("timestamp").reset_index(drop=True)
    return df
