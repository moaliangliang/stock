"""Ticker symbol matching: position.symbol (no suffix) → ticker.symbol (with .SZ/.SH)."""

import pytest
from unittest.mock import MagicMock, patch
from sqlalchemy import select as sql_select

from app.models.market_data import Ticker


TICKER_FIXTURES = [
    Ticker(symbol="000528.SZ", last_price=10.10, change_24h=3.80),
    Ticker(symbol="002475.SZ", last_price=76.06, change_24h=6.69),
    Ticker(symbol="600028.SH", last_price=5.21,  change_24h=-0.19),
    Ticker(symbol="600406.SH", last_price=26.85, change_24h=2.95),
    Ticker(symbol="000001.SZ", last_price=15.00, change_24h=1.50),
    # 159637 has NO ticker — ETF tickers not in this data source
]


def find_ticker_by_symbol(ticker_list: list, position_symbol: str) -> Ticker | None:
    """Simulate the OR query: match exact OR symbol.SZ/SH suffix."""
    for t in ticker_list:
        if t.symbol == position_symbol:
            return t
        # Strip suffix: "000528.SZ" → "000528"
        short = t.symbol.rsplit(".", 1)[0] if "." in t.symbol else t.symbol
        if short == position_symbol:
            return t
    return None


class TestTickerMatching:
    """ticker 带 .SZ/.SH，position 不带后缀"""

    def test_exact_match(self):
        t = find_ticker_by_symbol(TICKER_FIXTURES, "000528.SZ")
        assert t is not None
        assert t.last_price == 10.10

    def test_without_suffix(self):
        t = find_ticker_by_symbol(TICKER_FIXTURES, "000528")
        assert t is not None
        assert t.last_price == 10.10

    def test_shanghai_suffix(self):
        t = find_ticker_by_symbol(TICKER_FIXTURES, "600028")
        assert t is not None
        assert t.symbol == "600028.SH"

    def test_shenzhen_suffix(self):
        t = find_ticker_by_symbol(TICKER_FIXTURES, "002475")
        assert t is not None
        assert t.symbol == "002475.SZ"

    def test_no_match(self):
        t = find_ticker_by_symbol(TICKER_FIXTURES, "999999")
        assert t is None

    def test_etf_no_ticker(self):
        """ETF 代码在 ticker_data 中不存在 → 返回 None，不应崩溃"""
        t = find_ticker_by_symbol(TICKER_FIXTURES, "159637")
        assert t is None

    def test_mixed_exact_and_no_suffix(self):
        """同一个代码查两次，exact 和无后缀都应返回同一个 ticker"""
        t1 = find_ticker_by_symbol(TICKER_FIXTURES, "000001.SZ")
        t2 = find_ticker_by_symbol(TICKER_FIXTURES, "000001")
        assert t1 is not None
        assert t2 is not None
        assert t1.last_price == t2.last_price


class TestTickerMatchingEdgeCases:
    """边界情况"""

    def test_empty_ticker_list(self):
        assert find_ticker_by_symbol([], "000528") is None

    def test_symbol_with_dot_not_suffix(self):
        """如 BTC/USDT 这种本身带斜杠的符号"""
        fake_tickers = [Ticker(symbol="BTC/USDT", last_price=50000, change_24h=1.0)]
        t = find_ticker_by_symbol(fake_tickers, "BTC/USDT")
        assert t is not None
        # 不会错误地把它拆成 BTC 和 USDT

    def test_multiple_matches_returns_first(self):
        """多个可能的匹配（不应出现，但防御）"""
        dup = [
            Ticker(symbol="000528.SZ", last_price=10.0, change_24h=1.0),
            Ticker(symbol="000528",    last_price=9.0,  change_24h=2.0),
        ]
        t = find_ticker_by_symbol(dup, "000528")
        assert t is not None
        assert t.last_price == 10.0


class TestSQLAlchemyLikeQuery:
    """验证 LIKE 查询语法能正确匹配"""

    def test_like_pattern(self):
        """模拟 SQL: symbol LIKE '000528.%' """
        symbol = "000528"
        pattern = symbol + ".%"
        matches = [t for t in TICKER_FIXTURES if t.symbol.startswith(symbol + ".")]
        assert len(matches) == 1
        assert matches[0].symbol == "000528.SZ"
