"""Strategy signal generation: MA cross, MACD, KDJ, Bollinger, Grid."""
import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

from app.services.strategy import (
    ma_cross_strategy,
    macd_strategy,
    kdj_strategy,
    bollinger_strategy,
    grid_strategy,
)

# ── helpers ──────────────────────────────────────────────────────────

def _make_ohlcv(prices: list[float], with_hl: bool = False) -> pd.DataFrame:
    n = len(prices)
    base = datetime(2025, 1, 2)
    timestamps = [base + timedelta(days=i) for i in range(n)]
    df = pd.DataFrame({
        "timestamp": timestamps,
        "open": prices,
        "high": [p * 1.02 for p in prices] if with_hl else prices,
        "low":  [p * 0.98 for p in prices] if with_hl else prices,
        "close": prices,
        "volume": [1000000] * n,
    })
    if not with_hl:
        df["high"] = df["open"] * 1.02
        df["low"] = df["open"] * 0.98
    return df


def _make_trending(up: bool, n: int = 60) -> pd.DataFrame:
    """Generate a smooth trending series."""
    rng = np.random.RandomState(42)
    trend = 0.002 if up else -0.002
    prices = [100.0]
    for _ in range(n - 1):
        prices.append(prices[-1] * (1 + trend + rng.normal(0, 0.005)))
    return _make_ohlcv(prices)


# ══════════════════════════════════════════════════════════════════════
# MA Cross strategy
# ══════════════════════════════════════════════════════════════════════

class TestMACross:
    def test_bullish_cross(self):
        """快线上穿慢线 → 产生买入信号"""
        # Build a series where fast MA overtakes slow MA
        close = [10.0] * 30  # flat
        close += [10.5] * 5  # small bump triggers cross
        df = _make_ohlcv(close)
        signals = ma_cross_strategy(df, fast_period=5, slow_period=20)
        buys = [s for s in signals if s["action"] == "buy"]
        assert len(buys) >= 1

    def test_bearish_cross(self):
        """快线下穿慢线 → 产生卖出信号"""
        close = [20.0] * 30
        close += [19.0] * 5  # drop triggers death cross
        df = _make_ohlcv(close)
        signals = ma_cross_strategy(df, fast_period=5, slow_period=20)
        sells = [s for s in signals if s["action"] == "sell"]
        assert len(sells) >= 1

    def test_insufficient_data(self):
        """数据不足时返回空列表"""
        df = _make_ohlcv([10.0] * 10)
        signals = ma_cross_strategy(df, fast_period=5, slow_period=20)
        assert signals == []

    def test_no_cross_in_flat(self):
        """横盘无交叉 → 无信号"""
        df = _make_ohlcv([15.0] * 50)
        signals = ma_cross_strategy(df, fast_period=5, slow_period=20)
        assert signals == []

    def test_signal_has_required_fields(self):
        df = _make_trending(True, 50)
        signals = ma_cross_strategy(df)
        for s in signals:
            assert "timestamp" in s
            assert "action" in s
            assert "price" in s
            assert "reason" in s
            assert s["action"] in ("buy", "sell")


# ══════════════════════════════════════════════════════════════════════
# MACD strategy
# ══════════════════════════════════════════════════════════════════════

class TestMACD:
    def test_bullish_cross_in_uptrend(self):
        """上涨趋势中的MACD金叉 → 买入"""
        df = _make_trending(True, 60)
        signals = macd_strategy(df, fast=12, slow=26, signal=9)
        buys = [s for s in signals if s["action"] == "buy"]
        assert len(buys) >= 1

    def test_bearish_cross_in_downtrend(self):
        """下跌趋势中的MACD死叉 → 卖出"""
        df = _make_trending(False, 60)
        signals = macd_strategy(df, fast=12, slow=26, signal=9)
        sells = [s for s in signals if s["action"] == "sell"]
        assert len(sells) >= 1

    def test_insufficient_data(self):
        df = _make_ohlcv([10.0] * 20)
        signals = macd_strategy(df, fast=12, slow=26, signal=9)
        assert signals == []

    def test_macd_histogram_values(self):
        """信号中包含MACD值不应崩溃"""
        df = _make_trending(True, 60)
        signals = macd_strategy(df)
        for s in signals:
            assert "reason" in s
            assert isinstance(s["action"], str)


# ══════════════════════════════════════════════════════════════════════
# KDJ strategy
# ══════════════════════════════════════════════════════════════════════

class TestKDJ:
    def test_oversold_buy(self):
        """超卖区金叉 → 买入"""
        np.random.seed(123)
        n = 60
        prices = [30.0]
        highs = [30.0]
        lows = [30.0]

        # Phase 1: steady (bars 0-10)
        for _ in range(10):
            prices.append(prices[-1] + np.random.normal(0, 0.05))
            highs.append(prices[-1] * 1.03)
            lows.append(prices[-1] * 0.97)

        # Phase 2: crash (bars 11-20) — pushes K well below 20
        for _ in range(10):
            prices.append(prices[-1] - abs(np.random.normal(1.0, 0.3)))
            highs.append(prices[-1] * 1.03)
            lows.append(prices[-1] * 0.97)

        # Phase 3: strong recovery (bars 21-40) — creates golden cross
        for _ in range(20):
            prices.append(prices[-1] + abs(np.random.normal(1.5, 0.3)))
            highs.append(prices[-1] * 1.03)
            lows.append(prices[-1] * 0.97)

        # Phase 4: continued rise to confirm
        for _ in range(19):
            prices.append(prices[-1] + np.random.normal(0.8, 0.1))
            highs.append(prices[-1] * 1.03)
            lows.append(prices[-1] * 0.97)

        df = pd.DataFrame({
            "timestamp": [datetime(2025, 1, 2) + timedelta(days=i) for i in range(n)],
            "open": prices, "high": highs, "low": lows,
            "close": prices, "volume": [1000000] * n,
        })
        signals = kdj_strategy(df, n=9, k=3, d=3)
        buys = [s for s in signals if s["action"] == "buy"]
        assert len(buys) >= 1, f"Expected at least 1 buy signal, got {len(buys)}"

    def test_oversold_flag_only(self):
        """KDJ requires BOTH cross AND oversold/overbought condition"""
        # Pure cross without oversold/overbought should NOT fire
        close = [20.0] * 20
        close += [20.0 + i * 0.1 for i in range(20)]  # gentle rise, KDJ around 50
        df = _make_ohlcv(close)
        signals = kdj_strategy(df, n=9, k=3, d=3)
        # With prices slowly rising, K should stay roughly around 50 — no signals
        assert len(signals) == 0

    def test_insufficient_data(self):
        df = _make_ohlcv([10.0] * 10)
        signals = kdj_strategy(df)
        assert signals == []


# ══════════════════════════════════════════════════════════════════════
# Bollinger strategy
# ══════════════════════════════════════════════════════════════════════

class TestBollinger:
    def test_lower_band_touch_buy(self):
        """价格跌破下轨 → 买入"""
        np.random.seed(99)
        n = 50
        prices = [50.0]
        for i in range(n - 1):
            if 30 <= i <= 32:
                prices.append(prices[-1] * 0.92)  # crash below band
            elif 33 <= i <= 36:
                prices.append(prices[-1] * 1.05)  # bounce back
            else:
                prices.append(prices[-1] * (1 + np.random.normal(0, 0.01)))
        df = _make_ohlcv(prices)
        signals = bollinger_strategy(df, period=20, std=2.0)
        buys = [s for s in signals if s["action"] == "buy"]
        assert len(buys) >= 1, f"Expected buy signals near lower band, got {len(buys)}"

    def test_upper_band_touch_sell(self):
        """价格突破上轨 → 卖出"""
        np.random.seed(77)
        n = 50
        prices = [50.0]
        for i in range(n - 1):
            if 30 <= i <= 32:
                prices.append(prices[-1] * 1.10)  # surge above band
            else:
                prices.append(prices[-1] * (1 + np.random.normal(0, 0.005)))
        df = _make_ohlcv(prices)
        signals = bollinger_strategy(df, period=20, std=2.0)
        sells = [s for s in signals if s["action"] == "sell"]
        assert len(sells) >= 1, f"Expected sell signals near upper band, got {len(sells)}"

    def test_insufficient_data(self):
        df = _make_ohlcv([10.0] * 15)
        signals = bollinger_strategy(df, period=20)
        assert signals == []

    def test_bounce_from_lower_band(self):
        """价格从下轨反弹 → 产生bounce买入信号"""
        np.random.seed(55)
        n = 50
        prices = [50.0]
        for i in range(n - 1):
            if 15 <= i <= 20:
                prices.append(prices[-1] * 0.97)  # near lower band
            elif 21 <= i <= 26:
                prices.append(prices[-1] * 1.04)  # bounce
            else:
                prices.append(prices[-1] * (1 + np.random.normal(0, 0.005)))
        df = _make_ohlcv(prices)
        signals = bollinger_strategy(df)
        # Should have at least some buy signals (touch or bounce)
        buys = [s for s in signals if s["action"] == "buy"]
        assert len(buys) >= 1


# ══════════════════════════════════════════════════════════════════════
# Grid strategy
# ══════════════════════════════════════════════════════════════════════

class TestGrid:
    def test_grid_signals_with_oscillation(self):
        """震荡行情中产生买卖信号"""
        n = 80
        prices = [50.0]
        for i in range(n - 1):
            if i % 20 < 10:
                prices.append(prices[-1] * 1.01)
            else:
                prices.append(prices[-1] * 0.99)
        df = _make_ohlcv(prices)
        signals = grid_strategy(df, grid_levels=10, upper_price=60, lower_price=40)
        assert len(signals) >= 2  # at least a buy and a sell

    def test_auto_bounds(self):
        """自动推断上下界"""
        df = _make_ohlcv([50 + i * 0.1 for i in range(50)])
        signals = grid_strategy(df, grid_levels=5)
        assert isinstance(signals, list)

    def test_insufficient_data(self):
        df = _make_ohlcv([10.0])
        signals = grid_strategy(df)
        assert signals == []

    def test_bad_bounds(self):
        """上界≤下界时返回空"""
        df = _make_ohlcv(list(range(30, 10, -1)))
        signals = grid_strategy(df, grid_levels=5, upper_price=10, lower_price=20)
        assert signals == []

    def test_grid_signals_have_required_fields(self):
        df = _make_ohlcv([50 + i * 0.2 * ((-1) ** (i // 5)) for i in range(100)])
        signals = grid_strategy(df, grid_levels=5)
        for s in signals:
            assert "action" in s
            assert s["action"] in ("buy", "sell")
            assert "price" in s
            assert "reason" in s
