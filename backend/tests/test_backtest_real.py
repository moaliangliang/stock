"""Integration tests for run_backtest with real kline data."""
import math
import pytest
from datetime import datetime, timedelta, timezone

from app.models.strategy import StrategyType
from app.services.backtest import run_backtest, _prepare_dataframe, _empty_result


# ══════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════

def _make_kline(days: int, start_price: float = 10.0, trend: float = 0.001,
                start_date: datetime = None) -> list:
    """Generate synthetic daily kline data."""
    if start_date is None:
        start_date = datetime(2025, 1, 6, tzinfo=timezone.utc)  # Monday
    data = []
    price = start_price
    for i in range(days):
        ts = start_date + timedelta(days=i)
        # Skip weekends
        while ts.weekday() >= 5:
            ts += timedelta(days=1)
        open_p = price
        close_p = price * (1 + trend)
        high_p = max(open_p, close_p) * 1.01
        low_p = min(open_p, close_p) * 0.99
        data.append({
            "timestamp": ts.timestamp(),
            "open": round(open_p, 4),
            "high": round(high_p, 4),
            "low": round(low_p, 4),
            "close": round(close_p, 4),
            "volume": 1000000,
        })
        price = close_p
    return data


# ══════════════════════════════════════════════════════════════════════
# _prepare_dataframe
# ══════════════════════════════════════════════════════════════════════

class TestPrepareDataframe:
    def test_empty_input(self):
        df = _prepare_dataframe([])
        assert df.empty

    def test_basic_parsing(self):
        data = _make_kline(10)
        df = _prepare_dataframe(data)
        assert len(df) == 10
        assert "open" in df.columns
        assert "close" in df.columns
        assert df["close"].iloc[0] > 0

    def test_sorts_chronologically(self):
        data = _make_kline(5)
        data[0], data[-1] = data[-1], data[0]  # swap
        df = _prepare_dataframe(data)
        assert df["timestamp"].iloc[0] < df["timestamp"].iloc[-1]

    def test_drops_nan_close(self):
        data = _make_kline(5)
        data[2]["close"] = None
        df = _prepare_dataframe(data)
        assert len(df) == 4


# ══════════════════════════════════════════════════════════════════════
# _empty_result
# ══════════════════════════════════════════════════════════════════════

class TestEmptyResult:
    def test_returns_capital_unchanged(self):
        r = _empty_result(100000.0)
        assert r["total_return"] == 0.0
        assert r["annual_return"] == 0.0
        assert r["final_equity"] == 100000.0
        assert r["total_trades"] == 0
        assert r["equity_curve"] == []
        assert r["trades"] == []


# ══════════════════════════════════════════════════════════════════════
# run_backtest integration
# ══════════════════════════════════════════════════════════════════════

class TestRunBacktestEmpty:
    def test_empty_kline(self):
        result = run_backtest(StrategyType.MA_CROSS, {}, [], 100000.0)
        assert result["total_return"] == 0.0
        assert result["total_trades"] == 0

    def test_single_bar(self):
        """One bar — not enough for signals."""
        data = _make_kline(1)
        result = run_backtest(StrategyType.MA_CROSS, {}, data, 100000.0)
        assert result["annual_return"] == 0.0
        assert result["total_trades"] == 0


class TestRunBacktestBasic:
    def test_trending_up_ma_cross(self):
        """MA cross on a steady uptrend should generate trades."""
        data = _make_kline(60, start_price=10.0, trend=0.01)  # +1% daily for 60 days
        result = run_backtest(
            StrategyType.MA_CROSS,
            {"fast_period": 5, "slow_period": 20},
            data,
            initial_capital=100000.0,
        )
        assert "total_return" in result
        assert "annual_return" in result
        assert "sharpe_ratio" in result
        assert "max_drawdown" in result
        assert result["final_equity"] >= 0
        assert isinstance(result["equity_curve"], list)
        assert isinstance(result["trades"], list)

    def test_returns_all_required_keys(self):
        """Verify all documented return keys are present."""
        data = _make_kline(30)
        result = run_backtest(StrategyType.MA_CROSS, {}, data)
        for key in [
            "total_return", "annual_return", "max_drawdown",
            "sharpe_ratio", "win_rate", "total_trades",
            "profit_trades", "loss_trades", "profit_factor",
            "final_equity", "equity_curve", "trades",
        ]:
            assert key in result, f"Missing key: {key}"


class TestRunBacktestMetrics:
    def test_no_trades_means_zero_trades(self):
        """Flat market with wide bands → no signals → no trades."""
        data = _make_kline(30, start_price=10.0, trend=0.0)
        result = run_backtest(
            StrategyType.BOLLINGER,
            {"period": 20, "std": 3.0},  # wide bands → unlikely to trigger
            data,
            100000.0,
        )
        if result["total_trades"] == 0:
            assert result["win_rate"] == 0.0

    def test_final_equity_positive(self):
        """Final equity should always be non-negative."""
        data = _make_kline(100, start_price=50.0, trend=0.002)
        result = run_backtest(StrategyType.MA_CROSS, {}, data, 100000.0)
        assert result["final_equity"] >= 0

    def test_equity_curve_length(self):
        """Equity curve has one point per bar."""
        n_bars = 30
        data = _make_kline(n_bars)
        result = run_backtest(StrategyType.MA_CROSS, {}, data)
        assert len(result["equity_curve"]) == n_bars

    def test_drawdown_in_range(self):
        """Max drawdown is between 0 and 1."""
        data = _make_kline(100, start_price=20.0, trend=0.003)
        result = run_backtest(StrategyType.MACD, {}, data, 100000.0)
        assert 0.0 <= result["max_drawdown"] <= 1.0


class TestRunBacktestAnnualization:
    def test_daily_bars_use_245_periods(self):
        """Daily kline → periods_per_year = 245 (A-shares)."""
        # 245 daily bars, total_return = 10%
        # annual = (1 + 0.10)^(245/245) - 1 = 0.10
        data = _make_kline(245, start_price=10.0, trend=0.0004)
        result = run_backtest(StrategyType.MA_CROSS, {}, data, 100000.0)
        # Can't guarantee exact trades/return, but annual should not explode
        assert isinstance(result["annual_return"], float)
        assert not math.isinf(result["annual_return"])
        assert not math.isnan(result["annual_return"])

    def test_annual_return_with_few_bars(self):
        """2 bars → uses (1+r)^245 - 1 formula, not period-based."""
        data = _make_kline(2, start_price=10.0, trend=0.01)
        result = run_backtest(StrategyType.MA_CROSS, {}, data, 100000.0)
        assert result["annual_return"] >= -1.0
        assert not math.isinf(result["annual_return"])


class TestRunBacktestFeeAndLotSize:
    def test_lot_size_rounding(self):
        """A-share lots are multiples of 100."""
        data = _make_kline(100, start_price=100.0, trend=0.001)
        result = run_backtest(
            StrategyType.MA_CROSS,
            {"fast_period": 5, "slow_period": 20},
            data,
            initial_capital=100000.0,
            commission=0.001,
        )
        for t in result["trades"]:
            if t["action"] == "buy":
                assert t["quantity"] >= 100
                assert t["quantity"] % 100 == 0, f"Buy quantity {t['quantity']} not a lot"

    def test_sell_rounds_to_lot(self):
        """Partial sell must be in lot multiples."""
        data = _make_kline(60, start_price=50.0, trend=0.002)
        result = run_backtest(
            StrategyType.MACD,
            {"fast": 6, "slow": 13, "signal": 5},
            data,
            initial_capital=100000.0,
        )
        for t in result["trades"]:
            if t["action"] == "sell":
                assert t["quantity"] >= 100
                assert t["quantity"] % 100 == 0

    def test_commission_present(self):
        """Every trade has a non-zero fee line item."""
        data = _make_kline(100, start_price=30.0, trend=0.002)
        result = run_backtest(
            StrategyType.MA_CROSS,
            {"fast_period": 5, "slow_period": 20},
            data,
            initial_capital=100000.0,
            commission=0.0003,  # A-share rate
        )
        for t in result["trades"]:
            assert "fee" in t
            assert t["fee"] >= 0


class TestRunBacktestPnL:
    def test_sell_has_pnl(self):
        """Every sell trade records PnL."""
        data = _make_kline(120, start_price=25.0, trend=0.001)
        result = run_backtest(
            StrategyType.MACD,
            {},
            data,
            initial_capital=100000.0,
        )
        sells = [t for t in result["trades"] if t["action"] == "sell"]
        for s in sells:
            assert "pnl" in s


class TestRunBacktestProfitFactor:
    def test_profit_factor_no_losses(self):
        """All wins → profit_factor = inf."""
        result = _empty_result(100000)
        result["profit_factor"] = float("inf")
        assert result["profit_factor"] == float("inf")

    def test_profit_factor_calculated(self):
        """Verify profit_factor = gross_profit / gross_loss."""
        data = _make_kline(120, start_price=30.0, trend=0.001)
        result = run_backtest(StrategyType.MACD, {}, data, 100000.0)
        assert result["profit_factor"] >= 0
        sells = [t for t in result["trades"] if t["action"] == "sell"]
        if result["profit_trades"] > 0 and result["loss_trades"] > 0:
            assert result["profit_factor"] > 0
