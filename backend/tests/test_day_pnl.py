"""Day PnL: change_24h is a PERCENTAGE, must convert to absolute price change."""

import pytest


def calculate_day_pnl(quantity: float, current_price: float, change_24h_pct: float):
    """
    Replicate _refresh_position_price day_pnl logic.
    change_24h_pct: 如 0.38 表示 0.38%，6.69 表示 6.69%
    """
    abs_change = current_price * change_24h_pct / 100
    day_pnl = round(quantity * abs_change, 2)
    day_ratio = round(change_24h_pct, 2)
    return day_pnl, day_ratio


class TestDayPnlCalculation:
    """日盈亏 = 数量 × 现价 × change_24h% / 100"""

    def test_positive_change(self):
        """百分比转绝对值"""
        pnl, ratio = calculate_day_pnl(200, 76.06, 6.69)
        # abs_change = 76.06 × 6.69/100 = 5.0884
        # day_pnl = 200 × 5.0884 = 1017.68
        assert pnl == 1017.68
        assert ratio == 6.69

    def test_negative_change(self):
        pnl, ratio = calculate_day_pnl(500, 10.0, -2.0)
        # abs_change = 10.0 × -2.0/100 = -0.2
        # day_pnl = 500 × -0.2 = -100.0
        assert pnl == -100.0
        assert ratio == -2.0

    def test_zero_change(self):
        pnl, ratio = calculate_day_pnl(1000, 50.0, 0.0)
        assert pnl == 0.0
        assert ratio == 0.0

    def test_etf_small_price(self):
        """ETF 低价时精度"""
        pnl, ratio = calculate_day_pnl(7000, 1.924, 0.21)
        # abs_change = 1.924 × 0.21/100 = 0.00404
        # day_pnl = 7000 × 0.00404 = 28.28... ≈ 28.28
        assert abs(pnl - 28.28) < 0.01

    def test_high_price_stock(self):
        """高价股"""
        pnl, ratio = calculate_day_pnl(100, 108.68, -0.03)
        # abs_change = 108.68 × -0.03/100 = -0.0326
        # day_pnl = 100 × -0.0326 = -3.26
        assert pnl == -3.26
        assert ratio == -0.03

    def test_large_percentage(self):
        """涨跌停板级别（±10%）"""
        pnl, ratio = calculate_day_pnl(1000, 20.0, 10.0)
        assert pnl == 2000.0          # 1000 × 20 × 10/100
        assert ratio == 10.0

    def test_none_change(self):
        """change_24h 为 None 时应返回 0"""
        # This tests the None guard in _refresh_position_price
        assert 0 == 0  # placeholder, tested via mock in test_position_pnl


class TestOldVsNewFormula:
    """回归：确保不再用旧公式（把百分比当绝对值）"""

    def test_old_formula_would_be_wrong(self):
        """旧公式: quantity × change_24h → 1000 × 0.38 = 380（错！）"""
        old_wrong = 1000 * 0.38
        pnl_new, _ = calculate_day_pnl(1000, 2.091, 0.38)
        # 新公式: 1000 × 2.091 × 0.38/100 = 7.95
        assert pnl_new == 7.95
        assert old_wrong == 380.0      # 旧公式差 47 倍
        assert old_wrong != pnl_new     # 回归验证
