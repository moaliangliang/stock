"""PnL and PnL ratio calculation — includes negative cost (减仓回本) edge cases."""
import pytest


def calculate_pnl(quantity: float, cost_price: float, current_price: float):
    """Replicate _refresh_position_price PnL logic (extracted for testing)."""
    mv = round(quantity * current_price, 2)
    if cost_price > 0:
        pnl = round(quantity * (current_price - cost_price), 2)
        ratio = round((current_price - cost_price) / cost_price * 100, 2)
    else:
        pnl = round(mv + abs(quantity * cost_price), 2)
        ratio = round((current_price - cost_price) / current_price * 100, 2) if current_price > 0 else 0
    return pnl, ratio, mv


class TestPositiveCost:
    """正常正成本仓位"""

    def test_profitable_position(self):
        pnl, ratio, mv = calculate_pnl(100, 50.0, 55.0)
        assert pnl == 500.0
        assert ratio == 10.0
        assert mv == 5500.0

    def test_loss_position(self):
        pnl, ratio, mv = calculate_pnl(200, 100.0, 95.0)
        assert pnl == -1000.0
        assert ratio == -5.0
        assert mv == 19000.0

    def test_breakeven(self):
        pnl, ratio, mv = calculate_pnl(500, 30.0, 30.0)
        assert pnl == 0.0
        assert ratio == 0.0
        assert mv == 15000.0

    def test_etf_low_price(self):
        """ETF 价格<1元时的精度"""
        pnl, ratio, mv = calculate_pnl(10000, 0.966, 1.100)
        assert pnl == 1340.0
        assert ratio == 13.87
        assert mv == 11000.0


class TestNegativeCost:
    """负成本仓位 — 盈利减仓后剩余"""

    def test_small_negative_cost(self):
        """159637 真实数据：成本-0.166，现价1.065，100股"""
        pnl, ratio, mv = calculate_pnl(100, -0.166, 1.065)
        assert mv == 106.50
        assert pnl == 123.10
        assert ratio == 115.59

    def test_large_negative_cost(self):
        """大幅减仓后成本很负"""
        pnl, ratio, mv = calculate_pnl(50, -5.0, 10.0)
        assert mv == 500.0
        assert pnl == 750.0
        assert ratio == 150.0

    def test_negative_cost_zero_price(self):
        """现价为0时不应除零"""
        pnl, ratio, mv = calculate_pnl(100, -1.0, 0.0)
        assert mv == 0.0
        assert pnl == 100.0
        assert ratio == 0

    def test_ratio_not_negative_for_negative_cost(self):
        """负成本时盈亏比不应为负（关键回归测试）"""
        _, ratio, _ = calculate_pnl(100, -0.5, 2.0)
        assert ratio > 0, f"负成本盈亏比应为正，实际: {ratio}%"


class TestZeroCost:
    """零成本边界（刚买入、或异常数据）"""

    def test_zero_cost(self):
        pnl, ratio, mv = calculate_pnl(100, 0.0, 50.0)
        assert mv == 5000.0
        assert pnl == 5000.0
        assert ratio == 100.0
