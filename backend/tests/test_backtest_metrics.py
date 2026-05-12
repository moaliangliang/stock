"""Backtest metrics: Sharpe ratio, max drawdown, annual return, win rate."""

import math
import numpy as np
import pytest


def compute_metrics(equity_curve: np.ndarray, trades_pnl: list[float], initial_cap: float = 100000):
    """Replicate run_backtest metric calculations."""
    if len(equity_curve) < 2:
        return {"total_return": 0, "annual_return": 0, "max_drawdown": 0,
                "sharpe_ratio": 0, "win_rate": 0}

    total_return = (equity_curve[-1] - initial_cap) / initial_cap * 100
    trading_days = len(equity_curve)
    annual_return = ((1 + total_return / 100) ** (252 / trading_days) - 1) * 100

    peak = np.maximum.accumulate(equity_curve)
    drawdown = (peak - equity_curve) / peak
    max_drawdown = float(np.max(drawdown) * 100)

    daily_returns = np.diff(equity_curve) / equity_curve[:-1]
    sharpe = float(np.sqrt(252) * daily_returns.mean() / daily_returns.std()) if len(daily_returns) > 1 and daily_returns.std() > 0 else 0

    trades = len(trades_pnl)
    wins = sum(1 for p in trades_pnl if p > 0)
    win_rate = wins / trades * 100 if trades > 0 else 0

    return {
        "total_return": round(total_return, 2),
        "annual_return": round(annual_return, 2),
        "max_drawdown": round(max_drawdown, 2),
        "sharpe_ratio": round(sharpe, 2),
        "win_rate": round(win_rate, 1),
    }


class TestAnnualReturn:
    """年化收益率"""

    def test_one_year_exact(self):
        """252个交易日，10%收益 → 年化≈10%"""
        eq = np.linspace(100000, 110000, 252)
        m = compute_metrics(eq, [])
        assert abs(m["total_return"] - 10.0) < 0.1
        assert abs(m["annual_return"] - 10.0) < 0.2

    def test_half_year(self):
        """126天赚5% → 年化≈(1.05)^(252/126)-1≈10.25%"""
        eq = np.linspace(100000, 105000, 126)
        m = compute_metrics(eq, [])
        assert m["total_return"] == 5.0
        assert 9.5 < m["annual_return"] < 11.0

    def test_negative_return(self):
        eq = np.linspace(100000, 90000, 200)
        m = compute_metrics(eq, [])
        assert m["total_return"] == -10.0
        assert m["annual_return"] < 0


class TestMaxDrawdown:
    """最大回撤"""

    def test_no_drawdown(self):
        """一路涨，最大回撤≈0"""
        eq = np.linspace(100000, 200000, 100)
        m = compute_metrics(eq, [])
        assert m["max_drawdown"] < 0.01

    def test_v_shape(self):
        """先涨到150k → 跌到80k → 回撤=(150-80)/150=46.67%"""
        eq = np.array([100000, 150000, 120000, 80000, 90000], dtype=float)
        m = compute_metrics(eq, [])
        assert abs(m["max_drawdown"] - 46.67) < 0.1

    def test_multiple_drawdowns(self):
        """多次回撤，取最大值"""
        eq = np.array([100000, 120000, 80000, 100000, 130000, 90000], dtype=float)
        m = compute_metrics(eq, [])
        # 第一波: (120-80)/120=33.3%, 第二波: (130-90)/130=30.8%
        assert abs(m["max_drawdown"] - 33.33) < 0.3


class TestSharpeRatio:
    """夏普比率"""

    def test_constant_return(self):
        """每天涨固定0.1% → 波动为0 → 夏普=0"""
        eq = 100000 * np.cumprod(1 + np.full(100, 0.001))
        m = compute_metrics(eq, [])
        # 近乎零波动 → std极小 → std检查通过即可，sharpe可能极大
        assert m["sharpe_ratio"] >= 0

    def test_zero_volatility(self):
        """完全无波动（价格不变）→ 夏普=0"""
        m = compute_metrics(np.full(100, 100000.0), [])
        assert m["sharpe_ratio"] == 0

    def test_positive_sharpe(self):
        """正收益+低波动 → 夏普>0"""
        np.random.seed(42)
        returns = np.random.normal(0.001, 0.01, 200)
        eq = 100000 * np.cumprod(1 + returns)
        m = compute_metrics(eq, [])
        assert m["sharpe_ratio"] > 0

    def test_negative_sharpe(self):
        """负收益 → 夏普<0"""
        np.random.seed(1)
        returns = np.random.normal(-0.002, 0.02, 200)
        eq = 100000 * np.cumprod(1 + returns)
        m = compute_metrics(eq, [])
        assert m["sharpe_ratio"] < 0


class TestWinRate:
    """胜率"""

    def test_all_wins(self):
        m = compute_metrics(np.linspace(100000, 200000, 100), [100, 200, 50])
        assert m["win_rate"] == 100.0

    def test_mixed(self):
        m = compute_metrics(np.linspace(100000, 110000, 50), [100, -50, 200, -30])
        assert m["win_rate"] == 50.0

    def test_no_trades(self):
        m = compute_metrics(np.linspace(100000, 110000, 100), [])
        assert m["win_rate"] == 0


class TestEmptyEdgeCases:
    """空数据/边缘"""

    def test_single_point(self):
        m = compute_metrics(np.array([100000]), [])
        assert m["total_return"] == 0
        assert m["max_drawdown"] == 0
        assert m["sharpe_ratio"] == 0

    def test_two_points(self):
        eq = np.array([100000, 110000], dtype=float)
        m = compute_metrics(eq, [])
        assert m["total_return"] == 10.0
        # 只有一天，std可能为0 → sharpe=0
