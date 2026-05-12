"""Auto-trade helpers: drawdown factor, position sizing, Kelly fallback."""
import json
import os
import tempfile
import pytest
from unittest.mock import MagicMock, patch

from app.services.auto_trade import (
    _get_drawdown_factor,
    _build_remark,
    _get_kelly_fraction,
    _calc_position_size,
    LEVEL_RANK,
)


# ══════════════════════════════════════════════════════════════════════
# Drawdown factor
# ══════════════════════════════════════════════════════════════════════

class TestDrawdownFactor:
    def test_normal_no_drawdown(self):
        """无回撤时因子为1.0"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            peak_file = f.name
        try:
            with patch(
                "app.services.auto_trade.os.path.join",
                return_value=peak_file,
            ):
                # No peak file → total_value becomes the peak
                dd_pct, factor = _get_drawdown_factor(100000.0)
                assert dd_pct == 0.0
                assert factor == 1.0
        finally:
            os.unlink(peak_file)

    def test_moderate_drawdown(self):
        """5-10%回撤因子为0.75"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"peak": 110000.0}, f)
            peak_file = f.name
        try:
            with patch(
                "app.services.auto_trade.os.path.join",
                return_value=peak_file,
            ):
                dd_pct, factor = _get_drawdown_factor(100000.0)
                assert pytest.approx(dd_pct, abs=0.01) == 0.0909
                assert factor == 0.75
        finally:
            os.unlink(peak_file)

    def test_severe_drawdown(self):
        """10-20%回撤因子为0.5"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"peak": 120000.0}, f)
            peak_file = f.name
        try:
            with patch(
                "app.services.auto_trade.os.path.join",
                return_value=peak_file,
            ):
                dd_pct, factor = _get_drawdown_factor(100000.0)
                assert 0.10 <= dd_pct < 0.20
                assert factor == 0.5
        finally:
            os.unlink(peak_file)

    def test_stop_trading(self):
        """超过20%回撤因子为0（停止交易）"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"peak": 130000.0}, f)
            peak_file = f.name
        try:
            with patch(
                "app.services.auto_trade.os.path.join",
                return_value=peak_file,
            ):
                dd_pct, factor = _get_drawdown_factor(100000.0)
                assert dd_pct >= 0.20
                assert factor == 0.0
        finally:
            os.unlink(peak_file)

    def test_new_high_updates_peak(self):
        """新高时更新峰值文件"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"peak": 100000.0}, f)
            peak_file = f.name
        try:
            with patch(
                "app.services.auto_trade.os.path.join",
                return_value=peak_file,
            ):
                dd_pct, factor = _get_drawdown_factor(110000.0)
                assert dd_pct == 0.0
                assert factor == 1.0
                # Peak file should be updated
                with open(peak_file) as f:
                    data = json.load(f)
                    assert data["peak"] == 110000.0
        finally:
            os.unlink(peak_file)


# ══════════════════════════════════════════════════════════════════════
# Kelly fraction
# ══════════════════════════════════════════════════════════════════════

class TestKellyFraction:
    def test_fallback_no_db(self):
        """没有DB时使用默认配置值"""
        from app.core.config import settings
        kelly = _get_kelly_fraction("000001.SZ", db=None)
        assert kelly == settings.AUTO_TRADE_POSITION_PCT

    def test_fallback_on_empty_db(self):
        """没有回测数据时使用默认值"""
        db = MagicMock()
        db.execute.return_value.scalars.return_value.first.return_value = None
        from app.core.config import settings
        kelly = _get_kelly_fraction("000001.SZ", db=db)
        assert kelly == settings.AUTO_TRADE_POSITION_PCT


# ══════════════════════════════════════════════════════════════════════
# Remark builder
# ══════════════════════════════════════════════════════════════════════

class TestBuildRemark:
    def test_single_signal(self):
        signals = [{"type": "ma_cross", "action": "buy"}]
        remark = _build_remark("均线交叉", "BUY", 75, signals)
        assert "auto" in remark
        assert "均线交叉" in remark
        assert "BUY(75)" in remark
        assert "ma_cross" in remark

    def test_multiple_signals(self):
        signals = [
            {"type": "ma_cross", "action": "buy"},
            {"type": "macd", "action": "buy"},
        ]
        remark = _build_remark("复合策略", "STRONG_BUY", 88, signals)
        assert "ma_cross+macd" in remark

    def test_empty_signal_types(self):
        remark = _build_remark("无信号", "WATCH", 30, [])
        assert "auto" in remark
        assert "无信号" in remark


# ══════════════════════════════════════════════════════════════════════
# Position sizing
# ══════════════════════════════════════════════════════════════════════

class TestCalcPositionSize:
    def test_produces_round_lot(self):
        """A股必须100股整数倍"""
        with patch(
            "app.services.auto_trade._get_kelly_fraction",
            return_value=0.1,
        ), patch(
            "app.services.auto_trade._get_drawdown_factor",
            return_value=(0.0, 1.0),
        ), patch(
            "app.services.auto_trade._estimate_slippage",
            return_value=0.001,
        ):
            order_value, quantity, slippage = _calc_position_size(
                100000.0, 50.0, symbol="000001.SZ"
            )
            assert quantity >= 100
            assert quantity % 100 == 0
            assert order_value == quantity * 50.0

    def test_minimum_100_shares(self):
        """最少100股"""
        with patch(
            "app.services.auto_trade._get_kelly_fraction",
            return_value=0.001,  # very small fraction
        ), patch(
            "app.services.auto_trade._get_drawdown_factor",
            return_value=(0.0, 1.0),
        ), patch(
            "app.services.auto_trade._estimate_slippage",
            return_value=0.001,
        ):
            _, quantity, _ = _calc_position_size(100000.0, 100.0)
            assert quantity == 100  # floor to minimum


# ══════════════════════════════════════════════════════════════════════
# Level rank
# ══════════════════════════════════════════════════════════════════════

class TestLevelRank:
    def test_ordering(self):
        assert LEVEL_RANK["STRONG_BUY"] > LEVEL_RANK["BUY"]
        assert LEVEL_RANK["BUY"] > LEVEL_RANK["WATCH"]
        assert LEVEL_RANK["WATCH"] > LEVEL_RANK["NONE"]

    def test_all_levels_defined(self):
        for key in ("STRONG_BUY", "BUY", "WATCH", "NONE"):
            assert key in LEVEL_RANK
            assert LEVEL_RANK[key] >= 0
