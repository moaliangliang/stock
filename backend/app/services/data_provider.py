"""
行情数据提供器 — 支持东方财富真实数据 / akshare / mock，失败自动降级
"""
import asyncio
import logging
import random
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SyncSessionLocal
from app.core.market_constants import BASE_PRICES
from app.models.market_data import KLine, SymbolInfo, Ticker
from app.services.data_authenticity import (
    DataSource,
    should_allow_mock_fallback,
    validate_kline_ohlc,
    validate_price_plausibility,
    validate_quote_completeness,
)
from app.services.data_cross_validator import cross_validate_all_tickers, cross_validate_klines_for_symbol

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public API — Ticker 行情
# ---------------------------------------------------------------------------


def refresh_all_tickers(db: Session):
    """刷新所有活跃标的的最新行情。
    - eastmoney 模式：从东方财富拉取真实数据
    - sina 模式：从新浪财经拉取真实数据
    - mock 模式：在当前价格上叠加随机波动
    """
    symbols = _get_active_symbols(db)
    if not symbols:
        return

    if settings.MARKET_DATA_PROVIDER == "eastmoney":
        _refresh_tickers_from_eastmoney(db, symbols)
    elif settings.MARKET_DATA_PROVIDER == "sina":
        _refresh_tickers_from_sina(db, symbols)
    else:
        _refresh_tickers_mock(db, symbols)

    # 交叉校验：主源刷新完成后，从第二源拉取并对比
    if settings.CROSS_VALIDATION_ENABLED and settings.MARKET_DATA_PROVIDER not in ("mock", ""):
        _cross_validate_tickers(db, symbols)


def refresh_ticker(db: Session, symbol: str):
    """刷新单个标的行情"""
    sym = db.execute(
        select(SymbolInfo).where(SymbolInfo.symbol == symbol, SymbolInfo.status == "active")
    ).scalar_one_or_none()
    if not sym:
        return

    if settings.MARKET_DATA_PROVIDER == "eastmoney":
        _refresh_tickers_from_eastmoney(db, [sym])
    elif settings.MARKET_DATA_PROVIDER == "sina":
        _refresh_tickers_from_sina(db, [sym])
    else:
        _refresh_single_ticker_mock(db, sym)


# ---------------------------------------------------------------------------
# Public API — K 线
# ---------------------------------------------------------------------------


def fetch_real_klines(
    symbol: str,
    interval: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> Optional[List[Dict[str, Any]]]:
    """获取真实 K 线数据。优先东方财富，新浪财经，akshare 作为 fallback。"""
    # 仅支持 A 股代码格式
    if not symbol.endswith((".SH", ".SZ")):
        return None

    result: Optional[List[Dict[str, Any]]] = None

    # 按配置的 provider 优先尝试
    if settings.MARKET_DATA_PROVIDER == "sina":
        result = fetch_klines_from_sina(symbol, interval, start_date, end_date)
        if result:
            for k in result:
                k["data_source"] = DataSource.SINA.value

    # 东方财富
    if not result:
        result = fetch_klines_from_eastmoney(symbol, interval, start_date, end_date)
        if result:
            for k in result:
                k["data_source"] = DataSource.EASTMONEY.value

    # 新浪作为 fallback
    if not result and settings.MARKET_DATA_PROVIDER != "sina":
        result = fetch_klines_from_sina(symbol, interval, start_date, end_date)
        if result:
            for k in result:
                k["data_source"] = DataSource.SINA.value

    # 降级到 akshare（仅日线）
    if not result and interval == "1d":
        result = _fetch_klines_from_akshare(symbol, interval)
        if result:
            for k in result:
                k["data_source"] = DataSource.AKSHARE.value

    # 交叉校验：K线获取成功后，从第二源拉取并对比
    if settings.CROSS_VALIDATION_ENABLED and result:
        try:
            cross_validate_klines_for_symbol(
                symbol, interval, result,
                primary_source=settings.MARKET_DATA_PROVIDER,
                start_date=start_date,
                end_date=end_date,
            )
        except Exception:
            logger.debug("K线交叉校验异常 %s", symbol, exc_info=True)

    return result


def fetch_klines_from_eastmoney(
    symbol: str,
    interval: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> Optional[List[Dict[str, Any]]]:
    """从东方财富获取 K 线数据。"""
    try:
        from app.utils.eastmoney_client import fetch_kline

        return fetch_kline(symbol, interval, start_date, end_date, fqt=1)
    except Exception as e:
        logger.warning("东方财富K线获取失败 %s: %s", symbol, e)
        return None


# ---------------------------------------------------------------------------
# Ticker — 东方财富真实数据
# ---------------------------------------------------------------------------


def _refresh_tickers_from_eastmoney(db: Session, symbols: List[SymbolInfo]):
    """从东方财富拉取所有 A 股标的的实时行情并更新 Ticker 表。"""
    a_stocks = [s for s in symbols if s.symbol.endswith((".SH", ".SZ"))]
    if not a_stocks:
        return

    try:
        from app.utils.eastmoney_client import fetch_realtime_quotes

        quotes = fetch_realtime_quotes([s.symbol for s in a_stocks])
    except Exception as e:
        logger.warning("东方财富行情获取失败: %s", e)
        if should_allow_mock_fallback(context=f"eastmoney ticker refresh ({len(a_stocks)} symbols)"):
            _refresh_tickers_mock(db, symbols)
        return

    if not quotes:
        logger.info("东方财富返回空数据")
        if should_allow_mock_fallback(context="eastmoney returned empty quotes"):
            _refresh_tickers_mock(db, symbols)
        return

    symbol_list = [s.symbol for s in a_stocks]
    existing = _get_ticker_map(db, symbol_list)

    for sym in a_stocks:
        q = quotes.get(sym.symbol)
        if not q or q.get("last_price") is None:
            continue

        ticker = existing.get(sym.symbol)
        if ticker:
            _update_ticker_from_quote(ticker, q)
        else:
            _create_ticker_from_quote(db, sym, q)

    db.flush()


def _update_ticker_from_quote(ticker: Ticker, q: Dict[str, Any], source: str = "eastmoney"):
    """用实时行情数据更新已有 Ticker"""
    # Validate price plausibility before accepting
    price = q.get("last_price")
    if price is not None and not validate_price_plausibility(float(price), ticker.symbol):
        logger.warning("Ticker price implausible for %s: %s, rejecting update", ticker.symbol, price)
        return

    ticker.last_price = q["last_price"]
    ticker.high_24h = q.get("high")
    ticker.low_24h = q.get("low")
    ticker.volume_24h = q.get("volume")
    ticker.change_24h = q.get("change_pct")
    ticker.turnover_24h = q.get("amount")
    ticker.bid = round(q["last_price"] * 0.999, 2)
    ticker.ask = round(q["last_price"] * 1.001, 2)
    ticker.data_source = source
    ticker.updated_at = datetime.now(timezone.utc)


def _create_ticker_from_quote(db: Session, sym: SymbolInfo, q: Dict[str, Any], source: str = "eastmoney"):
    """用实时行情数据创建新 Ticker"""
    ticker = Ticker(
        symbol=sym.symbol,
        last_price=q["last_price"],
        bid=round(q["last_price"] * 0.999, 2),
        ask=round(q["last_price"] * 1.001, 2),
        high_24h=q.get("high"),
        low_24h=q.get("low"),
        volume_24h=q.get("volume"),
        change_24h=q.get("change_pct"),
        turnover_24h=q.get("amount"),
        data_source=source,
    )
    db.add(ticker)


# ---------------------------------------------------------------------------
# Ticker — 新浪财经真实数据
# ---------------------------------------------------------------------------


def _refresh_tickers_from_sina(db: Session, symbols: List[SymbolInfo]):
    """从新浪财经拉取所有 A 股标的的实时行情并更新 Ticker 表。"""
    a_stocks = [s for s in symbols if s.symbol.endswith((".SH", ".SZ"))]
    if not a_stocks:
        return

    try:
        from app.utils.sina_client import fetch_realtime_quotes

        quotes = fetch_realtime_quotes([s.symbol for s in a_stocks])
    except Exception as e:
        logger.warning("新浪财经行情获取失败: %s", e)
        if should_allow_mock_fallback(context=f"sina ticker refresh ({len(a_stocks)} symbols)"):
            _refresh_tickers_mock(db, symbols)
        return

    if not quotes:
        logger.info("新浪财经返回空数据")
        if should_allow_mock_fallback(context="sina returned empty quotes"):
            _refresh_tickers_mock(db, symbols)
        return

    symbol_list = [s.symbol for s in a_stocks]
    existing = _get_ticker_map(db, symbol_list)

    for sym in a_stocks:
        q = quotes.get(sym.symbol)
        if not q or q.get("last_price") is None:
            continue

        # 更新 symbol_info 中的 name
        if q.get("name") and not sym.name:
            sym.name = q["name"]

        ticker = existing.get(sym.symbol)
        if ticker:
            _update_ticker_from_quote(ticker, q, source="sina")
        else:
            _create_ticker_from_quote(db, sym, q, source="sina")

    db.flush()


def fetch_klines_from_sina(
    symbol: str,
    interval: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> Optional[List[Dict[str, Any]]]:
    """从新浪财经获取 K 线数据。"""
    try:
        from app.utils.sina_client import fetch_kline

        return fetch_kline(symbol, interval, start_date, end_date)
    except Exception as e:
        logger.warning("新浪财经K线获取失败 %s: %s", symbol, e)
        return None


# ---------------------------------------------------------------------------
# Ticker — Mock 模拟数据（降级 / 非 A 股标的）
# ---------------------------------------------------------------------------


def _refresh_tickers_mock(db: Session, symbols: List[SymbolInfo]):
    """Mock 模式：从最新K线收盘价同步ticker价格，确保数据一致性"""
    symbol_list = [s.symbol for s in symbols]
    existing = _get_ticker_map(db, symbol_list)

    for sym in symbols:
        ticker = existing.get(sym.symbol)

        # Try to sync ticker price from latest 1d kline close
        latest_kline = db.execute(
            select(KLine.close).where(
                KLine.symbol == sym.symbol,
                KLine.interval == "1d",
            ).order_by(KLine.timestamp.desc()).limit(1)
        ).scalar_one_or_none()

        if ticker:
            if latest_kline is not None:
                # Sync ticker to latest kline close with tiny noise (0.05%)
                base = float(latest_kline)
                ticker.last_price = round(base * (1 + random.uniform(-0.0005, 0.0005)), 2)
            else:
                _apply_random_fluctuation(ticker)
            ticker.data_source = DataSource.MOCK.value
        else:
            _create_mock_ticker(db, sym)

    db.flush()


def _refresh_single_ticker_mock(db: Session, sym: SymbolInfo):
    """Mock 模式：刷新单个标的，从K线同步价格"""
    ticker = db.execute(select(Ticker).where(Ticker.symbol == sym.symbol)).scalar_one_or_none()

    latest_kline = db.execute(
        select(KLine.close).where(
            KLine.symbol == sym.symbol,
            KLine.interval == "1d",
        ).order_by(KLine.timestamp.desc()).limit(1)
    ).scalar_one_or_none()

    if ticker:
        if latest_kline is not None:
            base = float(latest_kline)
            ticker.last_price = round(base * (1 + random.uniform(-0.0005, 0.0005)), 2)
        else:
            _apply_random_fluctuation(ticker)
        ticker.data_source = DataSource.MOCK.value
    else:
        _create_mock_ticker(db, sym)
    db.flush()


def _apply_random_fluctuation(ticker: Ticker):
    """在当前价格上叠加随机微调（±0.5%）模拟实时跳动"""
    price = ticker.last_price or BASE_PRICES.get(ticker.symbol, 100.0)
    change_pct = random.uniform(-0.005, 0.005)
    new_price = round(price * (1 + change_pct), 2)

    if ticker.last_price:
        ticker.change_24h = round((new_price - ticker.last_price) / ticker.last_price * 100, 2)
    ticker.last_price = new_price
    ticker.bid = round(new_price * 0.999, 2)
    ticker.ask = round(new_price * 1.001, 2)
    ticker.updated_at = datetime.now(timezone.utc)


def _create_mock_ticker(db: Session, sym: SymbolInfo):
    """为没有行情记录的标的创建初始模拟数据"""
    base = BASE_PRICES.get(sym.symbol, 100.0)
    ticker = Ticker(
        symbol=sym.symbol,
        last_price=base,
        bid=round(base * 0.999, 2),
        ask=round(base * 1.001, 2),
        bid_volume=random.uniform(100, 10000),
        ask_volume=random.uniform(100, 10000),
        high_24h=round(base * random.uniform(1.01, 1.08), 2),
        low_24h=round(base * random.uniform(0.92, 0.99), 2),
        volume_24h=random.uniform(100000, 10000000),
        change_24h=round(random.uniform(-5, 5), 2),
        turnover_24h=round(random.uniform(1000000, 100000000), 2),
        data_source=DataSource.MOCK.value,
    )
    db.add(ticker)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _cross_validate_tickers(db: Session, symbols: List[SymbolInfo]):
    """从第二数据源拉取行情并与主源对比，结果写入 SystemLog。"""
    a_stocks = [s for s in symbols if s.symbol.endswith((".SH", ".SZ"))]
    if not a_stocks:
        return

    stock_symbols = [s.symbol for s in a_stocks]
    primary_source = settings.MARKET_DATA_PROVIDER

    # 从主源直接拉取原始行情（与 refresh 函数只差不做 DB 更新）
    try:
        if primary_source == "eastmoney":
            from app.utils.eastmoney_client import fetch_realtime_quotes
            primary_quotes = fetch_realtime_quotes(stock_symbols) or {}
        else:
            from app.utils.sina_client import fetch_realtime_quotes
            primary_quotes = fetch_realtime_quotes(stock_symbols) or {}
    except Exception:
        logger.warning("交叉校验: 主源 %s 拉取失败", primary_source)
        return

    if not primary_quotes:
        return

    try:
        cross_validate_all_tickers(primary_quotes, primary_source)
    except Exception:
        logger.exception("交叉校验异常")


def _get_active_symbols(db: Session) -> List[SymbolInfo]:
    result = db.execute(select(SymbolInfo).where(SymbolInfo.status == "active"))
    return list(result.scalars().all())


def _get_ticker_map(db: Session, symbols: List[str]) -> Dict[str, Ticker]:
    result = db.execute(select(Ticker).where(Ticker.symbol.in_(symbols)))
    return {t.symbol: t for t in result.scalars().all()}


def _fetch_klines_from_akshare(symbol: str, interval: str) -> Optional[List[Dict[str, Any]]]:
    """通过 akshare 获取 K 线数据（仅日线），作为降级方案"""
    code = symbol.replace(".SH", "").replace(".SZ", "")
    try:
        import akshare as ak
        import pandas as pd

        df = ak.stock_zh_a_hist(
            symbol=code,
            period="daily",
            start_date="20200101",
            end_date=datetime.now(timezone.utc).strftime("%Y%m%d"),
            adjust="qfq",
        )
        if df is None or df.empty:
            return None

        result = []
        for _, row in df.iterrows():
            ts = pd.to_datetime(row["日期"]).to_pydatetime()
            result.append({
                "timestamp": ts.replace(tzinfo=timezone.utc),
                "open": float(row["开盘"]),
                "high": float(row["最高"]),
                "low": float(row["最低"]),
                "close": float(row["收盘"]),
                "volume": float(row.get("成交量", 0)),
                "amount": float(row.get("成交额", 0)),
            })
        return result
    except Exception as e:
        logger.warning("akshare K线获取失败 %s: %s", symbol, e)
        return None


# ---------------------------------------------------------------------------
# Async wrappers — for FastAPI endpoints using AsyncSession
# ---------------------------------------------------------------------------


def _run_in_sync_db(fn, *args, **kwargs):
    """在同步 Session 中运行函数，适用于 async 上下文。"""
    sdb = SyncSessionLocal()
    try:
        result = fn(sdb, *args, **kwargs)
        sdb.commit()
        return result
    except Exception:
        sdb.rollback()
        raise
    finally:
        sdb.close()


def _run_in_executor(fn, *args, **kwargs):
    """在默认线程池中运行同步函数（兼容 Python 3.7）。"""
    loop = asyncio.get_event_loop()
    import functools
    return loop.run_in_executor(None, functools.partial(fn, *args, **kwargs))


async def arefresh_all_tickers(_db=None):
    """Async wrapper: 刷新所有标的行情"""
    return await _run_in_executor(_run_in_sync_db, refresh_all_tickers)


async def arefresh_ticker(_db=None, symbol: str = ""):
    """Async wrapper: 刷新单个标的行情"""
    return await _run_in_executor(_run_in_sync_db, refresh_ticker, symbol)


async def afetch_real_klines(
    symbol: str,
    interval: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
):
    """Async wrapper: 获取真实 K 线"""
    return await _run_in_executor(fetch_real_klines, symbol, interval, start_date, end_date)


# ---------------------------------------------------------------------------
# Fundamental data (P4)
# ---------------------------------------------------------------------------

# 进程内基本面数据缓存（TTL 24小时），用数据库做跨进程共享
_fundamental_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_FUNDAMENTAL_CACHE_TTL = 86400  # 24小时（基本面数据按季度更新，日内不变）


def _db_cache_get(symbol: str) -> Optional[Dict[str, Any]]:
    """从 symbol_info.fundamental_cache 读缓存（跨进程共享）。"""
    try:
        from app.core.database import SyncSessionLocal
        from sqlalchemy import text
        import json
        db = SyncSessionLocal()
        try:
            row = db.execute(
                text("SELECT fundamental_cache, fundamental_cached_at FROM symbol_info WHERE symbol = :s"),
                {"s": symbol},
            ).fetchone()
            if row and row[0] and row[1]:
                import datetime
                cached_at = row[1]
                now = datetime.datetime.now(datetime.timezone.utc)
                if (now - cached_at).total_seconds() < _FUNDAMENTAL_CACHE_TTL:
                    return json.loads(row[0])
        finally:
            db.close()
    except Exception:
        pass
    return None


def _db_cache_set(symbol: str, data: Dict[str, Any]):
    """写 fundamental 结果到 symbol_info，供跨进程共享。"""
    try:
        from app.core.database import SyncSessionLocal
        from sqlalchemy import text
        import json
        db = SyncSessionLocal()
        try:
            db.execute(
                text("UPDATE symbol_info SET fundamental_cache = :d, fundamental_cached_at = datetime('now') WHERE symbol = :s"),
                {"d": json.dumps(data, ensure_ascii=False), "s": symbol},
            )
            db.commit()
        finally:
            db.close()
    except Exception:
        pass


def fetch_fundamental_data(symbol: str) -> Dict[str, Any]:
    """
    获取基本面数据。按优先级依次尝试：
      1. 东方财富妙想Skills（官方API，每月300次）
      2. akshare 新浪财经财务分析接口
      3. baostock（免费TCP数据服务）

    结果带两层缓存：
      - 进程内存缓存（极快）
      - 数据库缓存（跨进程共享，Celery 和 API 不重复消耗配额）
    """
    # 1. 进程内缓存
    now = time.time()
    if symbol in _fundamental_cache:
        cached_at, cached_data = _fundamental_cache[symbol]
        if now - cached_at < _FUNDAMENTAL_CACHE_TTL:
            return cached_data

    # 2. 数据库缓存（跨进程）
    db_cached = _db_cache_get(symbol)
    if db_cached:
        _fundamental_cache[symbol] = (now, db_cached)
        return db_cached

    # 3. 真实请求
    result = _fetch_fundamental_uncached(symbol)
    _fundamental_cache[symbol] = (now, result)
    _db_cache_set(symbol, result)
    return result


def invalidate_fundamental_cache(symbol: Optional[str] = None):
    """清除基本面缓存。不传 symbol 则清空全部。"""
    if symbol:
        _fundamental_cache.pop(symbol, None)
    else:
        _fundamental_cache.clear()


def _fetch_fundamental_uncached(symbol: str) -> Dict[str, Any]:
    if settings.MARKET_DATA_PROVIDER == "mock":
        return _mock_fundamental_data(symbol)

    # 1. 东方财富妙想Skills（官方API，数据最准）
    if settings.EASTMONEY_SKILLS_API_KEY:
        try:
            from app.utils.eastmoney_skills_client import fetch_fundamental as em_fetch
            em_data = em_fetch(symbol)
            if em_data:
                # PE 偶因 NLP 解析不稳定而缺失，用 baostock 补齐
                if em_data["pe"] <= 0 or em_data["pb"] <= 0:
                    bs = _fetch_fundamental_baostock(symbol)
                    if bs:
                        if em_data["pe"] <= 0 and bs["pe"] > 0:
                            em_data["pe"] = bs["pe"]
                        if em_data["pb"] <= 0 and bs["pb"] > 0:
                            em_data["pb"] = bs["pb"]
                return em_data
        except Exception as e:
            logger.warning("东方财富Skills基本面获取失败 %s: %s", symbol, e)

    code = symbol.replace(".SH", "").replace(".SZ", "")

    # 2. akshare 新浪财经财务分析接口
    try:
        import akshare as ak
        from datetime import date as dt_date
        # 从去年开始拉取，确保在当前年份数据未出时有历史数据可用
        start_year = str(dt_date.today().year - 1)
        df = ak.stock_financial_analysis_indicator(symbol=code, start_year=start_year)
        if df is not None and not df.empty:
            latest = df.iloc[-1]  # 最新报告期（日期升序，最后一条为最新）
            # 获取当前 ticker 价格用于计算 PE/PB
            pe = 0.0
            pb = 0.0
            eps = float(latest.get("摊薄每股收益(元)", 0) or 0)
            bvps = float(latest.get("每股净资产_调整前(元)", 0) or 0)

            # 根据报告期对 EPS 进行全年化：季报 EPS 需折算为年度 EPS
            report_date = latest.get("日期")
            if isinstance(report_date, dt_date) and eps > 0:
                month = report_date.month
                if month == 3:
                    eps = eps * 4       # Q1 → 全年化
                elif month == 6:
                    eps = eps * 2       # 半年报 → 全年化
                elif month == 9:
                    eps = eps * 4 / 3   # Q3 → 全年化
                # month == 12: full-year EPS, no adjustment

            try:
                from app.core.database import SyncSessionLocal
                from sqlalchemy import select
                from app.models.market_data import Ticker
                sdb = SyncSessionLocal()
                try:
                    ticker = sdb.execute(
                        select(Ticker).where(Ticker.symbol == symbol)
                    ).scalar_one_or_none()
                    if ticker and ticker.last_price and ticker.last_price > 0:
                        price = float(ticker.last_price)
                        if eps > 0:
                            pe = round(price / eps, 2)
                        if bvps > 0:
                            pb = round(price / bvps, 2)
                finally:
                    sdb.close()
            except Exception:
                pass

            return {
                "pe": pe,
                "pb": pb,
                "roe": float(latest.get("净资产收益率(%)", 0) or 0),
                "revenue_growth": float(latest.get("主营业务收入增长率(%)", 0) or 0),
                "profit_growth": float(latest.get("净利润增长率(%)", 0) or 0),
                "source": "sina",
            }
    except Exception as e:
        logger.warning("新浪财经基本面数据获取失败 %s: %s", symbol, e)

    # Try baostock as secondary source (free, no registration, stable TCP-based API)
    baostock_data = _fetch_fundamental_baostock(symbol)
    if baostock_data:
        return baostock_data

    # Fallback to mock only if explicitly configured
    if should_allow_mock_fallback(context=f"fundamental data for {symbol}"):
        return _mock_fundamental_data(symbol)

    # Real provider configured but data unavailable — return zeros rather than fake data
    logger.warning("基本面数据不可用 %s: 返回零值而非模拟数据", symbol)
    return {
        "pe": 0, "pb": 0, "roe": 0,
        "revenue_growth": 0, "profit_growth": 0,
        "source": "unavailable",
    }


def _mock_fundamental_data(symbol: str) -> Dict[str, Any]:
    """Generate realistic mock fundamental data based on symbol hash."""
    import hashlib
    h = hashlib.md5(symbol.encode()).digest()
    seed = int.from_bytes(h[:4], "big") / (2 ** 32)

    # PE: 8-80, median ~25
    pe = round(8 + seed * 72, 2) if seed < 0.95 else round(100 + seed * 100, 2)
    # PB: 0.5-10, median ~3
    pb = round(0.5 + (1 - seed) * 9.5, 2)
    # ROE: -5 to 35, median ~12
    roe = round(-5 + seed * 40, 2)
    # Revenue growth: -20 to 50, median ~12
    rev_growth = round(-20 + seed * 70, 2)
    # Profit growth: -30 to 60, median ~15
    prof_growth = round(-30 + seed * 90, 2)

    return {
        "pe": pe,
        "pb": pb,
        "roe": roe,
        "revenue_growth": rev_growth,
        "profit_growth": prof_growth,
        "source": "mock",
    }


def _fetch_fundamental_baostock(symbol: str) -> Optional[Dict[str, Any]]:
    """通过 baostock 获取基本面数据，用作 akshare 失败时的二级兜底。

    baostock 是基于 TCP 协议的自建数据库服务（非爬虫），API 稳定，
    免费免注册，提供 PE(TTM)/PB(MRQ)/ROE/营收增长/利润增长等指标。
    """
    try:
        import baostock as bs
        from datetime import date, timedelta
    except ImportError:
        return None

    if symbol.endswith(".SH"):
        bs_symbol = "sh." + symbol[:-3]
    elif symbol.endswith(".SZ"):
        bs_symbol = "sz." + symbol[:-3]
    else:
        return None

    try:
        lg = bs.login()
        if lg.error_code != "0":
            logger.warning("baostock 登录失败: %s", lg.error_msg)
            return None

        pe = 0.0
        pb = 0.0
        roe = 0.0
        revenue_growth = 0.0
        profit_growth = 0.0

        # 1. PE / PB from latest daily K-line (peTTM, pbMRQ)
        today = date.today()
        end_date = today.strftime("%Y-%m-%d")
        start_date = (today - timedelta(days=30)).strftime("%Y-%m-%d")

        rs = bs.query_history_k_data_plus(
            bs_symbol,
            "date,close,peTTM,pbMRQ",
            start_date=start_date,
            end_date=end_date,
            frequency="d",
            adjustflag="3",
        )
        if rs.error_code == "0":
            rows = []
            while rs.next():
                rows.append(rs.get_row_data())
            for row in reversed(rows):
                pe_val = float(row[2]) if len(row) > 2 and row[2] else 0.0
                pb_val = float(row[3]) if len(row) > 3 and row[3] else 0.0
                if pe_val > 0:
                    pe = pe_val
                    pb = pb_val
                    break

        # 2. ROE + revenue growth from latest quarterly profit data
        quarter = (today.month - 1) // 3 + 1
        year = today.year

        for offset in range(8):
            q = quarter - offset
            y = year
            if q <= 0:
                q += 4
                y -= 1

            rs = bs.query_profit_data(code=bs_symbol, year=y, quarter=q)
            if rs.error_code != "0":
                continue
            rows = []
            while rs.next():
                rows.append(rs.get_row_data())
            if not rows:
                continue

            row = rows[0]
            # roeAvg at index 3, MBRevenue at index 8
            if roe == 0 and len(row) > 3 and row[3]:
                roe = float(row[3]) * 100  # ratio → percentage

            if revenue_growth == 0 and len(row) > 8 and row[8]:
                rev_current = float(row[8])
                rs_prev = bs.query_profit_data(code=bs_symbol, year=y - 1, quarter=q)
                if rs_prev.error_code == "0":
                    prev_rows = []
                    while rs_prev.next():
                        prev_rows.append(rs_prev.get_row_data())
                    if prev_rows and len(prev_rows[0]) > 8 and prev_rows[0][8]:
                        rev_prev = float(prev_rows[0][8])
                        if rev_prev != 0:
                            revenue_growth = (rev_current - rev_prev) / abs(rev_prev) * 100

            if roe != 0 and revenue_growth != 0:
                break

        # 3. Profit growth from latest quarterly growth data (YOYPNI)
        for offset in range(8):
            q = quarter - offset
            y = year
            if q <= 0:
                q += 4
                y -= 1

            rs = bs.query_growth_data(code=bs_symbol, year=y, quarter=q)
            if rs.error_code != "0":
                continue
            rows = []
            while rs.next():
                rows.append(rs.get_row_data())
            if rows and len(rows[0]) > 7 and rows[0][7]:
                profit_growth = float(rows[0][7]) * 100  # ratio → percentage
                break

        bs.logout()

        # Require at least PE > 0 to consider the result valid
        if pe <= 0:
            return None

        return {
            "pe": round(pe, 2),
            "pb": round(pb, 2),
            "roe": round(roe, 2),
            "revenue_growth": round(revenue_growth, 2),
            "profit_growth": round(profit_growth, 2),
            "source": "baostock",
        }

    except Exception as e:
        try:
            bs.logout()
        except Exception:
            pass
        logger.warning("baostock 基本面数据获取失败 %s: %s", symbol, e)
        return None


# ---------------------------------------------------------------------------
# Mock data generator (for testing)
# ---------------------------------------------------------------------------


def mock_market_data(
    base_price: float = 50000.0,
    days: int = 365,
    interval_minutes: int = 60,
    volatility: float = 0.02,
    start_date: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """Generate mock kline data for testing purposes."""
    now = datetime.now(timezone.utc)
    interval_delta = timedelta(minutes=interval_minutes)
    total_bars = int((days * 24 * 60) / interval_minutes) + 1
    if start_date is None:
        start_date = now - timedelta(minutes=(total_bars - 1) * interval_minutes)
    data: List[Dict[str, Any]] = []

    price = base_price
    current_ts = start_date

    for _ in range(total_bars):
        change_pct = random.gauss(0, volatility * (interval_minutes / (24 * 60)) ** 0.5)
        next_price = price * (1 + change_pct)

        high_price = max(price, next_price) * (1 + abs(random.gauss(0, volatility * 0.3)))
        low_price = min(price, next_price) * (1 - abs(random.gauss(0, volatility * 0.3)))

        volume = random.uniform(100, 10000) * (1 + abs(change_pct) * 10)
        amount = volume * (price + next_price) / 2

        data.append({
            "timestamp": int(current_ts.timestamp()),
            "open": round(price, 2),
            "high": round(high_price, 2),
            "low": round(low_price, 2),
            "close": round(next_price, 2),
            "volume": round(volume, 4),
            "amount": round(amount, 2),
            "data_source": DataSource.MOCK.value,
        })

        price = next_price
        current_ts += interval_delta

    return data
