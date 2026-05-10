"""
Backtesting service - simulate strategy performance on historical data.
"""
from __future__ import annotations
import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select

import numpy as np

from app.models.strategy import StrategyType


def run_backtest(
    strategy_type: StrategyType,
    params: Dict[str, Any],
    kline_data: List[Dict[str, Any]],
    initial_capital: float = 10000.0,
    commission: float = 0.001,
    slippage: float = 0.001,
) -> Dict[str, Any]:
    """
    Run a full backtest simulation for a given strategy on historical kline data.

    The simulation processes each bar sequentially, evaluates the strategy for
    signals, executes trades, tracks positions, and computes performance metrics.

    Args:
        strategy_type: The type of strategy to simulate
            (from app.models.strategy.StrategyType).
        params: Strategy parameters (forwarded to the signal generator).
        kline_data: List of kline dicts with keys:
            timestamp, open, high, low, close, volume.
            Must be sorted chronologically (oldest first).
        initial_capital: Starting capital for the simulation.
        commission: Trading commission ratio (e.g. 0.001 = 0.1 %).
        slippage: Slippage ratio applied to fill prices
            (e.g. 0.001 = 0.1 % adverse move).

    Returns:
        A dict containing:
            - total_return (float): Total return as a decimal.
            - annual_return (float): Annualised return as a decimal.
            - max_drawdown (float): Maximum peak-to-trough drawdown as decimal.
            - sharpe_ratio (float): Annualised Sharpe ratio (risk-free = 0).
            - win_rate (float): Ratio of profitable trades to all trades.
            - total_trades (int): Total number of trades.
            - profit_trades (int): Number of profitable trades.
            - loss_trades (int): Number of losing trades.
            - profit_factor (float): Gross profit / gross loss.
            - equity_curve (List[float, float]): List of
              [timestamp_unix, equity_value] pairs.
            - trades (List[Dict]): List of trade records.
    """
    # ------------------------------------------------------------------
    # 1. Prepare data & generate signals
    # ------------------------------------------------------------------
    df = _prepare_dataframe(kline_data)
    signals = _generate_signals(strategy_type, params, df)

    if df.empty:
        return _empty_result(initial_capital)

    # ------------------------------------------------------------------
    # 2. Simulation state
    # ------------------------------------------------------------------
    cash = initial_capital
    position = 0.0  # number of units held
    equity_curve: List[List[float]] = []
    trades: List[Dict[str, Any]] = []
    open_trade: Optional[Dict[str, Any]] = None

    # Track daily returns for Sharpe ratio
    daily_returns: List[float] = []

    prev_equity = initial_capital
    peak_equity = initial_capital
    max_drawdown = 0.0

    # Signal index for O(1) lookup {timestamp: signal}
    signal_map: Dict[int, List[Dict[str, Any]]] = {}
    for sig in signals:
        ts = int(sig["timestamp"])
        signal_map.setdefault(ts, []).append(sig)

    # ------------------------------------------------------------------
    # 3. Walk through each bar
    # ------------------------------------------------------------------
    for idx, row in df.iterrows():
        ts = int(row["timestamp"].timestamp())
        close_price = row["close"]
        high_price = row["high"]
        low_price = row["low"]

        # -- Equity value at this bar --
        current_equity = cash + position * close_price
        equity_curve.append([ts * 1000, round(current_equity, 2)])  # ms for charts

        # Track daily return
        if prev_equity > 0:
            daily_ret = (current_equity - prev_equity) / prev_equity
            daily_returns.append(daily_ret)
        prev_equity = current_equity

        # Peak / drawdown tracking
        if current_equity > peak_equity:
            peak_equity = current_equity
        drawdown = (peak_equity - current_equity) / peak_equity if peak_equity > 0 else 0
        if drawdown > max_drawdown:
            max_drawdown = drawdown

        # -- Process signals at this timestamp --
        bar_signals = signal_map.get(ts, [])

        for sig in bar_signals:
            action = sig.get("action", "").lower()
            sig_price = sig.get("price", close_price)

            # Apply slippage
            if action == "buy":
                fill_price = sig_price * (1 + slippage)
            else:
                fill_price = sig_price * (1 - slippage)

            fee = fill_price * sig.get("quantity", 1) * commission

            if action == "buy" and cash > 0:
                # Use all available cash (simple approach)
                qty = (cash * 0.99) / fill_price  # reserve 1 % for fees
                cost = qty * fill_price
                total_cost = cost + cost * commission

                if total_cost <= cash:
                    position += qty
                    cash -= total_cost

                    open_trade = {
                        "entry_time": ts,
                        "entry_price": fill_price,
                        "quantity": qty,
                        "fee": cost * commission,
                    }

                    trades.append({
                        "timestamp": ts,
                        "action": "buy",
                        "price": round(fill_price, 4),
                        "quantity": round(qty, 6),
                        "fee": round(cost * commission, 4),
                        "reason": sig.get("reason", ""),
                    })

            elif action == "sell" and position > 0:
                qty = position  # sell entire position
                proceeds = qty * fill_price
                total_proceeds = proceeds - proceeds * commission

                cash += total_proceeds
                pnl = total_proceeds - (qty * (open_trade or {}).get("entry_price", 0))
                position = 0.0

                if open_trade:
                    trades.append({
                        "timestamp": ts,
                        "action": "sell",
                        "price": round(fill_price, 4),
                        "quantity": round(qty, 6),
                        "fee": round(proceeds * commission, 4),
                        "pnl": round(pnl, 4),
                        "reason": sig.get("reason", ""),
                    })
                    open_trade = None

    # Close any remaining position at the last price
    if position > 0 and len(df) > 0:
        last_row = df.iloc[-1]
        close_price = last_row["close"]
        fill_price = close_price * (1 - slippage)
        qty = position
        proceeds = qty * fill_price
        total_proceeds = proceeds - proceeds * commission
        cash += total_proceeds

        trades.append({
            "timestamp": int(last_row["timestamp"].timestamp()),
            "action": "sell",
            "price": round(fill_price, 4),
            "quantity": round(qty, 6),
            "fee": round(proceeds * commission, 4),
            "pnl": round(total_proceeds - (qty * (open_trade or {}).get("entry_price", 0)), 4),
            "reason": "Position closed at end of backtest",
        })
        position = 0.0

    # ------------------------------------------------------------------
    # 4. Compute performance metrics
    # ------------------------------------------------------------------
    final_equity = cash  # position is always 0 here
    total_return = (final_equity - initial_capital) / initial_capital if initial_capital > 0 else 0.0

    # Annualised return
    if len(df) > 1:
        days = (df["timestamp"].iloc[-1] - df["timestamp"].iloc[0]).total_seconds() / 86400.0
        years = max(days / 365.0, 1 / 365.0)
        annual_return = (1 + total_return) ** (1 / years) - 1
    else:
        annual_return = 0.0

    # Trade statistics
    buy_trades = [t for t in trades if t["action"] == "buy"]
    sell_trades = [t for t in trades if t["action"] == "sell"]

    profit_trades = len([t for t in sell_trades if t.get("pnl", 0) > 0])
    loss_trades = len([t for t in sell_trades if t.get("pnl", 0) <= 0])
    total_trades = len(sell_trades)

    win_rate = profit_trades / total_trades if total_trades > 0 else 0.0

    # Profit factor
    gross_profit = sum(t.get("pnl", 0) for t in sell_trades if t.get("pnl", 0) > 0)
    gross_loss = abs(sum(t.get("pnl", 0) for t in sell_trades if t.get("pnl", 0) < 0))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    # Sharpe ratio (annualised)
    if len(daily_returns) > 1:
        avg_return = np.mean(daily_returns)
        std_return = np.std(daily_returns, ddof=1)
        # Annualise: assuming ~252 trading days / year
        sharpe_ratio = (avg_return / std_return * math.sqrt(252)) if std_return > 0 else 0.0
    else:
        sharpe_ratio = 0.0

    return {
        "total_return": round(total_return, 6),
        "annual_return": round(annual_return, 6),
        "max_drawdown": round(max_drawdown, 6),
        "sharpe_ratio": round(sharpe_ratio, 4),
        "win_rate": round(win_rate, 4),
        "total_trades": total_trades,
        "profit_trades": profit_trades,
        "loss_trades": loss_trades,
        "profit_factor": round(profit_factor, 4),
        "final_equity": round(final_equity, 2),
        "equity_curve": equity_curve,
        "trades": trades,
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _prepare_dataframe(kline_data: List[Dict[str, Any]]) -> pd.DataFrame:
    """Convert raw kline data to a sorted DataFrame with datetime index."""
    import pandas as pd
    if not kline_data:
        return pd.DataFrame()

    df = pd.DataFrame(kline_data)

    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s", utc=True)

    for col in ["open", "high", "low", "close", "volume"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=["close"]).sort_values("timestamp").reset_index(drop=True)
    return df


def _generate_signals(
    strategy_type: StrategyType,
    params: Dict[str, Any],
    df: pd.DataFrame,
) -> List[Dict[str, Any]]:
    """Delegate signal generation to the appropriate strategy template."""
    # Late import to avoid circular dependency
    from app.services.strategy import (
        bollinger_strategy,
        grid_strategy,
        kdj_strategy,
        ma_cross_strategy,
        macd_strategy,
    )

    if df.empty:
        return []

    if strategy_type == StrategyType.MA_CROSS:
        signals = ma_cross_strategy(
            df,
            fast_period=params.get("fast_period", 5),
            slow_period=params.get("slow_period", 20),
        )
    elif strategy_type == StrategyType.MACD:
        signals = macd_strategy(
            df,
            fast=params.get("fast", 12),
            slow=params.get("slow", 26),
            signal=params.get("signal", 9),
        )
    elif strategy_type == StrategyType.KDJ:
        signals = kdj_strategy(
            df,
            n=params.get("n", 9),
            k=params.get("k", 3),
            d=params.get("d", 3),
        )
    elif strategy_type == StrategyType.BOLLINGER:
        signals = bollinger_strategy(
            df,
            period=params.get("period", 20),
            std=params.get("std", 2.0),
        )
    elif strategy_type == StrategyType.GRID:
        signals = grid_strategy(
            df,
            grid_levels=params.get("grid_levels", 10),
            upper_price=params.get("upper_price"),
            lower_price=params.get("lower_price"),
        )
    else:
        return []

    # Normalize timestamps to int (Unix seconds)
    for sig in signals:
        ts = sig.get("timestamp")
        if ts is not None:
            if hasattr(ts, 'timestamp'):
                sig["timestamp"] = int(ts.timestamp())
            else:
                sig["timestamp"] = int(ts)

    return signals


def _empty_result(initial_capital: float) -> Dict[str, Any]:
    """Return a zeroed-out result dict for empty input data."""
    return {
        "total_return": 0.0,
        "annual_return": 0.0,
        "max_drawdown": 0.0,
        "sharpe_ratio": 0.0,
        "win_rate": 0.0,
        "total_trades": 0,
        "profit_trades": 0,
        "loss_trades": 0,
        "profit_factor": 0.0,
        "final_equity": round(initial_capital, 2),
        "equity_curve": [],
        "trades": [],
    }


async def get_backtest_history(db, user_id: int, skip: int = 0, limit: int = 50, strategy_id: Optional[int] = None) -> list:
    """获取回测历史记录"""
    from app.models.strategy import StrategyRunLog, Strategy
    conditions = [Strategy.user_id == user_id]
    if strategy_id is not None:
        conditions.append(StrategyRunLog.strategy_id == strategy_id)
    stmt = (
        select(StrategyRunLog, Strategy.name, Strategy.type)
        .join(Strategy, StrategyRunLog.strategy_id == Strategy.id)
        .where(*conditions)
        .order_by(StrategyRunLog.run_time.desc())
        .offset(skip)
        .limit(limit)
    )
    result = await db.execute(stmt)
    rows = result.all()
    return [
        {
            "id": row.StrategyRunLog.id,
            "strategy_id": row.StrategyRunLog.strategy_id,
            "user_id": user_id,
            "strategy_name": row.name,
            "strategy_type": row.type.value if row.type else None,
            "run_time": row.StrategyRunLog.run_time.isoformat() if row.StrategyRunLog.run_time else None,
            "status": row.StrategyRunLog.status,
            "message": row.StrategyRunLog.message,
            "signals": row.StrategyRunLog.signals,
            "duration_ms": row.StrategyRunLog.duration_ms,
        }
        for row in rows
    ]


async def get_backtest_by_id(db, backtest_id: int):
    """获取单个回测记录"""
    from app.models.strategy import StrategyRunLog
    result = await db.execute(select(StrategyRunLog).where(StrategyRunLog.id == backtest_id))
    return result.scalar_one_or_none()
